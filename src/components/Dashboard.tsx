import { useState, useEffect, useRef } from 'react';
import {
  VscChevronLeft, VscGraph, VscDatabase, VscFile, VscSymbolMisc,
  VscFilter, VscSymbolNumeric, VscTable,
} from 'react-icons/vsc';
import {
  searchObjects, getObjectInfo, getObjectDependencies,
  createReportInstanceForSql, getReportSqlView,
  createCubeInstanceForSql, getCubeSqlView, getCubeSqlViewDirect,
  getV2ReportDefinition, getV2CubeDefinition,
  getCubeModelDefinition, getSystemHierarchy,
} from '../api/mstrClient';
import { useApp } from '../context/AppContext';

// ----- types -----

interface Category {
  label: string;
  type: number;
  icon: typeof VscGraph;
  color: string;
  bgColor: string;
}

interface ObjectRow {
  id: string;
  name: string;
  type: number;
  subtype?: number;
  owner?: string;
  dateModified?: string;
}

interface LineageNode {
  id: string;
  name: string;
  type: number;
  subtype?: number;
  children: LineageNode[];
  sql?: string;
  loading?: boolean;
  expanded?: boolean;
  error?: string;
}

const CATEGORIES: Category[] = [
  { label: 'Reports', type: 768, icon: VscGraph, color: 'text-blue-400', bgColor: 'from-blue-900/40 to-blue-900/10' },
  { label: 'Cubes', type: 776, icon: VscDatabase, color: 'text-green-400', bgColor: 'from-green-900/40 to-green-900/10' },
  { label: 'Dossiers', type: 55, icon: VscFile, color: 'text-purple-400', bgColor: 'from-purple-900/40 to-purple-900/10' },
  { label: 'Documents', type: 39, icon: VscSymbolMisc, color: 'text-yellow-400', bgColor: 'from-yellow-900/40 to-yellow-900/10' },
  { label: 'Metrics', type: 4, icon: VscSymbolNumeric, color: 'text-red-400', bgColor: 'from-red-900/40 to-red-900/10' },
  { label: 'Filters', type: 1, icon: VscFilter, color: 'text-cyan-400', bgColor: 'from-cyan-900/40 to-cyan-900/10' },
  { label: 'Tables', type: 10, icon: VscTable, color: 'text-orange-400', bgColor: 'from-orange-900/40 to-orange-900/10' },
];

const TYPE_LABELS: Record<number, string> = {
  1: 'Filter', 3: 'Report', 4: 'Metric', 7: 'Attribute', 8: 'Folder',
  10: 'Table', 12: 'Prompt', 13: 'Function', 14: 'Attribute Form',
  21: 'Cube', 39: 'Document', 47: 'Consolidation', 55: 'Dossier',
  768: 'Report', 776: 'Cube',
};

// Map search subtypes back to object types for API calls
function objectType(type: number, subtype?: number): number {
  if (subtype === 776) return 21;  // Intelligent Cube
  if (subtype === 768) return 3;   // Grid/SQL Report
  if (type === 768) return 3;
  if (type === 776) return 21;
  return type;
}

// ===== Main component =====

export default function Dashboard() {
  const { currentProject } = useApp();
  const [subView, setSubView] = useState<'overview' | 'list' | 'lineage'>('overview');
  const [selectedCategory, setSelectedCategory] = useState<Category | null>(null);
  const [selectedObject, setSelectedObject] = useState<ObjectRow | null>(null);

  // --- overview ---
  const [counts, setCounts] = useState<Record<number, number | null>>({});
  const [countsLoading, setCountsLoading] = useState(false);
  const dataLoaded = useRef(false);

  // --- list ---
  const [objects, setObjects] = useState<ObjectRow[]>([]);
  const [listLoading, setListLoading] = useState(false);
  const [searchName, setSearchName] = useState('');
  const listCache = useRef<Record<string, ObjectRow[]>>({});

  // --- lineage cache ---
  const lineageCache = useRef<Record<string, {
    info: any; def: any; root: LineageNode;
  }>>({});

  // --- system hierarchy cache (once per project) ---
  const systemHierarchyCache = useRef<{ relationships: { parent: any; child: any }[] } | null>(null);
  const systemHierarchyLoading = useRef<Promise<any> | null>(null);

  // --- lineage ---
  const [lineageRoot, setLineageRoot] = useState<LineageNode | null>(null);
  const [lineageLoading, setLineageLoading] = useState(false);
  const [objectInfoJson, setObjectInfoJson] = useState<any>(null);
  const [objectDefJson, setObjectDefJson] = useState<any>(null);

  // ===== Load counts on mount (once only) =====
  useEffect(() => {
    // Clear lineage cache on mount so new logic takes effect
    lineageCache.current = {};
    systemHierarchyCache.current = null;
    if (dataLoaded.current) return;
    dataLoaded.current = true;
    loadCounts();
  }, []);

  async function loadCounts() {
    setCountsLoading(true);
    const results = await Promise.allSettled(
      CATEGORIES.map(c => searchObjects({ type: c.type, limit: 1 }))
    );
    const newCounts: Record<number, number | null> = {};
    results.forEach((r, i) => {
      if (r.status === 'fulfilled') {
        const data = r.value;
        newCounts[CATEGORIES[i].type] = data.totalItems ?? data.result?.length ?? null;
      } else {
        newCounts[CATEGORIES[i].type] = null;
      }
    });
    setCounts(newCounts);
    setCountsLoading(false);
  }

  // ===== List: load objects =====
  async function loadList(cat: Category, name?: string) {
    setListLoading(true);
    try {
      const params: any = { type: cat.type, limit: 200 };
      if (name) params.name = name;
      const data = await searchObjects(params);
      const rows: ObjectRow[] = (data.result || []).map((o: any) => ({
        id: o.id, name: o.name, type: o.type, subtype: o.subtype,
        owner: o.owner?.name, dateModified: o.dateModified,
      }));
      const cacheKey = `${cat.type}:${name || ''}`;
      listCache.current[cacheKey] = rows;
      setObjects(rows);
    } catch {
      setObjects([]);
    } finally {
      setListLoading(false);
    }
  }

  function openCategory(cat: Category) {
    setSelectedCategory(cat);
    setSubView('list');
    setSearchName('');
    restoreListFromCache(cat, '');
  }

  function goBackToList() {
    if (!selectedCategory) return;
    setSubView('list');
    restoreListFromCache(selectedCategory, searchName);
  }

  function restoreListFromCache(cat: Category, name: string) {
    const cacheKey = `${cat.type}:${name || ''}`;
    if (listCache.current[cacheKey]) {
      setObjects(listCache.current[cacheKey]);
    } else {
      loadList(cat, name || undefined);
    }
  }

  // ===== Lineage =====
  async function fetchDefinition(objId: string, objType: number): Promise<any> {
    if (objType === 3) {
      try { return await getV2ReportDefinition(objId); } catch { return null; }
    }
    if (objType === 21) {
      // Try model API first, then V2
      try { return await getCubeModelDefinition(objId); } catch { /* fall through */ }
      try { return await getV2CubeDefinition(objId); } catch { return null; }
    }
    return null;
  }

  // Fetch system hierarchy once per project, with dedup
  async function getOrFetchSystemHierarchy(): Promise<{ relationships: { parent: any; child: any }[] }> {
    if (systemHierarchyCache.current) return systemHierarchyCache.current;
    if (systemHierarchyLoading.current) return systemHierarchyLoading.current;
    const promise = (async () => {
      try {
        const data = await getSystemHierarchy();
        console.log('[Dashboard] systemHierarchy raw response keys:', Object.keys(data || {}));
        // Try multiple possible response structures
        let rels = data?.relationships
          || data?.hierarchies?.[0]?.relationships
          || data?.result?.relationships
          || [];
        // If response is an array at top level
        if (Array.isArray(data)) rels = data;
        console.log('[Dashboard] systemHierarchy relationships count:', rels.length);
        if (rels.length > 0) {
          console.log('[Dashboard] sample relationship:', JSON.stringify(rels[0]));
        }
        const result = { relationships: rels };
        systemHierarchyCache.current = result;
        return result;
      } catch (err) {
        console.error('[Dashboard] systemHierarchy fetch failed:', err);
        const empty = { relationships: [] };
        // Don't cache failures — allow retry next time
        return empty;
      } finally {
        systemHierarchyLoading.current = null;
      }
    })();
    systemHierarchyLoading.current = promise;
    return promise;
  }

  // Build cube hierarchy by cross-referencing cube attributes with system hierarchy
  function buildCubeHierarchyNodes(
    cubeAttrs: { id: string; name: string; forms?: any[] }[],
    sysRels: { parent: any; child: any }[],
  ): LineageNode[] {
    const attrIds = new Set(cubeAttrs.map(a => a.id));
    const attrMap = new Map(cubeAttrs.map(a => [a.id, a]));

    // System hierarchy uses objectId; cube definition uses id — handle both
    const cubeRels = sysRels.filter(r => {
      const pid = r.parent?.objectId || r.parent?.id;
      const cid = r.child?.objectId || r.child?.id;
      return attrIds.has(pid) && attrIds.has(cid);
    });

    console.log('[Dashboard] cube attr IDs:', [...attrIds]);
    console.log('[Dashboard] matching cubeRels:', cubeRels.length);

    // Build parent→children map
    const childrenOf = new Map<string, string[]>();
    const hasParent = new Set<string>();
    for (const rel of cubeRels) {
      const pid = rel.parent.objectId || rel.parent.id;
      const cid = rel.child.objectId || rel.child.id;
      if (!childrenOf.has(pid)) childrenOf.set(pid, []);
      childrenOf.get(pid)!.push(cid);
      hasParent.add(cid);
    }

    // Root attributes = those with no parent in the cube
    const roots = cubeAttrs.filter(a => !hasParent.has(a.id));

    // Recursive builder
    function buildNode(attrId: string): LineageNode {
      const attr = attrMap.get(attrId)!;
      const kidIds = childrenOf.get(attrId) || [];
      const kidNodes = kidIds.map(cid => buildNode(cid));

      // Also add attribute forms as leaves
      const formNodes: LineageNode[] = (attr.forms || []).map((f: any) => ({
        id: f.id, name: `${f.name}${f.dataType ? ' (' + f.dataType + ')' : ''}`,
        type: 14, children: [], expanded: false,
      }));

      return {
        id: attr.id, name: attr.name, type: 7,
        children: [...kidNodes, ...formNodes],
        expanded: kidNodes.length > 0 || formNodes.length > 0,
      };
    }

    return roots.map(r => buildNode(r.id));
  }

  // Build a lineage tree from definition + dependencies + SQL.
  async function buildTree(
    objId: string, objType: number,
  ): Promise<{ children: LineageNode[]; sql?: string; def?: any }> {
    // For cubes: also fetch system hierarchy in parallel
    const isCube = objType === 21;
    const [defRes, depsRes, sqlRes, sysHierRes] = await Promise.allSettled([
      fetchDefinition(objId, objType),
      getObjectDependencies(objId),
      (objType === 3 || objType === 21) ? fetchSql(objId, objType) : Promise.resolve(undefined),
      isCube ? getOrFetchSystemHierarchy() : Promise.resolve({ relationships: [] }),
    ]);

    const def = defRes.status === 'fulfilled' ? defRes.value : null;
    const sql = sqlRes.status === 'fulfilled' ? sqlRes.value : undefined;
    const sysHier = sysHierRes.status === 'fulfilled' ? sysHierRes.value : { relationships: [] };

    if (isCube) {
      console.log('[Dashboard] cube def keys:', Object.keys(def || {}));
      console.log('[Dashboard] cube def?.definition keys:', Object.keys(def?.definition || {}));
      console.log('[Dashboard] cube sql result:', sql ? 'got SQL' : 'no SQL');
      console.log('[Dashboard] sysHier rels count:', sysHier.relationships.length);
    }

    let depList: any[] = [];
    if (depsRes.status === 'fulfilled') {
      const deps = depsRes.value;
      depList = Array.isArray(deps) ? deps : deps?.dependencies || [];
    }

    const children: LineageNode[] = [];

    // --- For cubes: use system hierarchy for dimensional hierarchy ---
    if (isCube) {
      // Extract cube attributes from V2 definition
      // V2 cube def has: template.rows (attrs), template.columns[0].elements (metrics)
      // Also try: definition.grid, definition.availableObjects, top-level attributes/metrics
      const cubeAttrs =
        def?.template?.rows ||
        def?.definition?.grid?.rows ||
        def?.definition?.availableObjects?.attributes ||
        def?.attributes || [];
      const cubeMetrics =
        def?.template?.columns?.[0]?.elements ||
        def?.definition?.grid?.columns?.[0]?.elements ||
        def?.definition?.availableObjects?.metrics ||
        def?.metrics || [];

      console.log('[Dashboard] cubeAttrs count:', cubeAttrs.length, cubeAttrs.map((a: any) => a.name));
      console.log('[Dashboard] cubeMetrics count:', cubeMetrics.length);

      if (cubeAttrs.length > 0 && sysHier.relationships.length > 0) {
        // Build proper dimensional hierarchy from system hierarchy
        const hierNodes = buildCubeHierarchyNodes(cubeAttrs, sysHier.relationships);
        children.push({
          id: 'group-hierarchy', name: `Attribute Hierarchy (${cubeAttrs.length} attributes)`,
          type: 7, children: hierNodes, expanded: true,
        });
      } else if (cubeAttrs.length > 0) {
        // Fallback: flat list if no system hierarchy available
        const attrNodes: LineageNode[] = cubeAttrs.map((a: any) => {
          const forms = (a.forms || []).map((f: any) => ({
            id: f.id, name: `${f.name}${f.dataType ? ' (' + f.dataType + ')' : ''}`,
            type: 14, children: [], expanded: false,
          }));
          return { id: a.id, name: a.name, type: 7, children: forms, expanded: forms.length > 0 };
        });
        children.push({
          id: 'group-attributes', name: `Attributes (${attrNodes.length})`,
          type: 7, children: attrNodes, expanded: true,
        });
      }

      if (cubeMetrics.length > 0) {
        const metricNodes: LineageNode[] = cubeMetrics.map((m: any) => ({
          id: m.id, name: m.name, type: 4, children: [], expanded: false,
        }));
        children.push({
          id: 'group-metrics', name: `Metrics (${metricNodes.length})`,
          type: 4, children: metricNodes, expanded: true,
        });
      }
    } else {
      // --- Reports: original logic ---
      const grid = def?.definition?.grid;
      const available = def?.definition?.availableObjects;
      const attrSource = grid?.rows || available?.attributes || [];
      const metricSource = grid?.columns?.[0]?.elements || available?.metrics || [];
      const defAttrs = attrSource.length > 0 ? attrSource : def?.attributes || [];
      const defMetrics = metricSource.length > 0 ? metricSource : def?.metrics || [];
      const defFilters = def?.definition?.grid?.filter || def?.filter;

      if (defAttrs.length > 0) {
        const attrNodes: LineageNode[] = defAttrs.map((a: any) => {
          const forms = (a.forms || []).map((f: any) => ({
            id: f.id, name: `${f.name}${f.dataType ? ' (' + f.dataType + ')' : ''}`,
            type: 14, children: [], expanded: false,
          }));
          return { id: a.id, name: a.name, type: 7, children: forms, expanded: forms.length > 0 };
        });
        children.push({
          id: 'group-attributes', name: `Attributes (${attrNodes.length})`,
          type: 7, children: attrNodes, expanded: true,
        });
      }

      if (defMetrics.length > 0) {
        const metricNodes: LineageNode[] = defMetrics.map((m: any) => ({
          id: m.id, name: m.name, type: 4, children: [], expanded: false,
        }));
        children.push({
          id: 'group-metrics', name: `Metrics (${metricNodes.length})`,
          type: 4, children: metricNodes, expanded: true,
        });
      }

      if (defFilters && Object.keys(defFilters).length > 0) {
        children.push({
          id: 'group-filter', name: 'Filter',
          type: 1, children: [{
            id: 'filter-def', name: JSON.stringify(defFilters),
            type: 1, children: [], expanded: false,
          }], expanded: true,
        });
      }
    }

    // --- Source objects from dependencies (tables, cubes, prompts, etc.) ---
    const sourceTypes = new Set([10, 21, 3, 12, 1, 8, 39, 55, 47, 13]);
    const sourceDeps = depList.filter(d => sourceTypes.has(d.type));

    if (sourceDeps.length > 0) {
      const byType = new Map<number, any[]>();
      for (const d of sourceDeps) {
        if (!byType.has(d.type)) byType.set(d.type, []);
        byType.get(d.type)!.push(d);
      }

      const typeOrder = [21, 3, 10, 12, 8];
      const sortedTypes = [...byType.keys()].sort((a, b) => {
        const ai = typeOrder.indexOf(a); const bi = typeOrder.indexOf(b);
        return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
      });

      const sourceChildren: LineageNode[] = [];
      for (const t of sortedTypes) {
        const items = byType.get(t)!;
        const label = TYPE_LABELS[t] || `Type ${t}`;
        const nodes: LineageNode[] = items.map((d: any) => ({
          id: d.id || d.objectId, name: d.name, type: d.type, subtype: d.subtype,
          children: [], expanded: false,
        }));
        if (nodes.length === 1) {
          sourceChildren.push(nodes[0]);
        } else {
          sourceChildren.push({
            id: `group-source-${t}`, name: `${label}s (${nodes.length})`,
            type: t, children: nodes, expanded: true,
          });
        }
      }

      children.push({
        id: 'group-sources', name: `Data Sources (${sourceDeps.length})`,
        type: 10, children: sourceChildren, expanded: true,
      });
    }

    return { children, sql, def };
  }

  async function openLineage(obj: ObjectRow) {
    setSelectedObject(obj);
    setSubView('lineage');

    const cacheKey = obj.id;
    const cached = lineageCache.current[cacheKey];
    if (cached) {
      setObjectInfoJson(cached.info);
      setObjectDefJson(cached.def);
      setLineageRoot(cached.root);
      return;
    }

    setLineageLoading(true);
    setObjectInfoJson(null);
    setObjectDefJson(null);
    setLineageRoot(null);

    // Fetch info and build tree (tree already fetches definition internally)
    // For object info: try with type, fallback without type if 404
    const fetchInfo = async () => {
      try {
        return await getObjectInfo(obj.id, objectType(obj.type, obj.subtype));
      } catch {
        try { return await getObjectInfo(obj.id); } catch { return null; }
      }
    };
    const [infoRes, treeRes] = await Promise.allSettled([
      fetchInfo(),
      buildTree(obj.id, objectType(obj.type, obj.subtype)),
    ]);

    const info = infoRes.status === 'fulfilled' ? infoRes.value : null;
    setObjectInfoJson(info);

    const tree = treeRes.status === 'fulfilled' ? treeRes.value : { children: [] as LineageNode[], sql: undefined, def: null };

    const def = tree.def || null;
    setObjectDefJson(def);

    const root: LineageNode = {
      id: obj.id, name: obj.name, type: objectType(obj.type, obj.subtype),
      children: tree.children, sql: tree.sql, expanded: true,
    };
    setLineageRoot(root);

    lineageCache.current[cacheKey] = { info, def, root };
    setLineageLoading(false);
  }

  async function fetchSql(objId: string, objType: number): Promise<string | undefined> {
    try {
      if (objType === 3) {
        const inst = await createReportInstanceForSql(objId);
        const instId = inst.instanceId || inst.id;
        const sqlData = await getReportSqlView(objId, instId);
        return sqlData.sqlStatement || sqlData.sql || JSON.stringify(sqlData, null, 2);
      }
      if (objType === 21) {
        // Check for Free Form SQL cube first (SQL embedded in model definition)
        try {
          const modelDef = await getCubeModelDefinition(objId);
          const sourceType = modelDef?.information?.sourceType || modelDef?.sourceType;
          console.log('[Dashboard] cube model sourceType:', sourceType);
          if (sourceType === 'custom_sql_free_form') {
            const freeFormSql = modelDef?.dataSource?.table?.physicalTable
              ?.sqlExpression?.tree?.children?.[0]?.variant?.value;
            if (freeFormSql) return freeFormSql;
          }
        } catch (e) {
          console.log('[Dashboard] cube model def fetch failed (expected for non-free-form):', e);
        }

        // Try direct sqlView first (no instance needed)
        try {
          console.log('[Dashboard] trying direct cube sqlView...');
          const sqlData = await getCubeSqlViewDirect(objId);
          console.log('[Dashboard] direct cube sqlView keys:', Object.keys(sqlData || {}));
          const sqlText = sqlData.sqlStatement || sqlData.sql;
          if (sqlText) return sqlText;
          return JSON.stringify(sqlData, null, 2);
        } catch {
          console.log('[Dashboard] direct sqlView not available, trying instance approach...');
        }

        // Fallback: create instance then get sqlView
        console.log('[Dashboard] creating cube instance for SQL...');
        const inst = await createCubeInstanceForSql(objId);
        console.log('[Dashboard] cube instance response keys:', Object.keys(inst || {}));
        const instId = inst.instanceId || inst.id;
        console.log('[Dashboard] cube instanceId:', instId);

        try {
          const sqlData = await getCubeSqlView(objId, instId);
          console.log('[Dashboard] cube sqlView response keys:', Object.keys(sqlData || {}));
          return sqlData.sqlStatement || sqlData.sql || JSON.stringify(sqlData, null, 2);
        } catch {
          console.log('[Dashboard] instance sqlView not available either');
          const instSql = inst?.definition?.sql || inst?.sql;
          if (instSql) return typeof instSql === 'string' ? instSql : JSON.stringify(instSql, null, 2);
          return undefined;
        }
      }
    } catch (err) {
      console.error('[Dashboard] fetchSql failed:', err);
      return undefined;
    }
    return undefined;
  }

  // Manual expand for nodes that weren't auto-expanded (e.g. at depth limit)
  async function expandNode(node: LineageNode) {
    if (!selectedObject) return;
    if (node.expanded && node.children.length > 0) {
      node.expanded = false;
      setLineageRoot({ ...lineageRoot! });
      return;
    }

    node.loading = true;
    node.expanded = true;
    setLineageRoot({ ...lineageRoot! });

    try {
      const tree = await buildTree(node.id, node.type);
      node.children = tree.children;
      node.sql = tree.sql;
    } catch (err: any) {
      node.error = err.message || 'Failed to load dependencies';
    } finally {
      node.loading = false;
      setLineageRoot({ ...lineageRoot! });
    }
  }

  // ===== Breadcrumb =====
  function renderBreadcrumb() {
    const parts: { label: string; onClick?: () => void }[] = [
      { label: 'Dashboard', onClick: () => setSubView('overview') },
    ];
    if (subView === 'list' && selectedCategory) {
      parts.push({ label: selectedCategory.label });
    }
    if (subView === 'lineage') {
      if (selectedCategory) {
        parts.push({ label: selectedCategory.label, onClick: () => goBackToList() });
      }
      if (selectedObject) {
        parts.push({ label: selectedObject.name });
      }
    }
    return (
      <div className="flex items-center gap-2 text-sm">
        {parts.map((p, i) => (
          <span key={i} className="flex items-center gap-2">
            {i > 0 && <span className="text-gray-600">/</span>}
            {p.onClick ? (
              <button onClick={p.onClick} className="text-blue-400 hover:text-blue-300 transition-colors">{p.label}</button>
            ) : (
              <span className="text-gray-300">{p.label}</span>
            )}
          </span>
        ))}
      </div>
    );
  }

  // ===== Render: Overview =====
  function renderOverview() {
    return (
      <div className="p-6">
        <h2 className="text-lg font-bold text-white mb-2">Dashboard</h2>
        <p className="text-xs text-gray-500 mb-1">
          {currentProject ? currentProject.name : 'No project selected'}
        </p>
        <p className="text-xs text-gray-600 mb-6">
          Live counts from MicroStrategy. Reports = grid/SQL reports only (subtype 768); dossiers and documents are counted separately.
        </p>
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4">
          {CATEGORIES.map(cat => {
            const count = counts[cat.type];
            return (
              <button
                key={cat.type}
                onClick={() => openCategory(cat)}
                className={`bg-gradient-to-br ${cat.bgColor} border border-[#1e2d50] rounded-xl p-5 text-left hover:border-[#3a4d70] transition-all group`}
              >
                <div className="flex items-center justify-between mb-3">
                  <cat.icon className={`text-2xl ${cat.color}`} />
                  {countsLoading ? (
                    <div className="w-8 h-6 bg-gray-700/50 rounded animate-pulse" />
                  ) : (
                    <span className="text-2xl font-bold text-white">
                      {count != null ? count : '?'}
                    </span>
                  )}
                </div>
                <div className="text-sm text-gray-300 group-hover:text-white transition-colors">{cat.label}</div>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  // ===== Render: Object List =====
  function renderList() {
    if (!selectedCategory) return null;
    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Search + count */}
        <div className="px-6 py-3 border-b border-[#1e2d50] flex items-center gap-3">
          <input
            type="text"
            placeholder="Search by name..."
            value={searchName}
            onChange={e => setSearchName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') loadList(selectedCategory, searchName); }}
            className="flex-1 max-w-sm px-3 py-2 bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg text-sm text-white placeholder-gray-600 focus:outline-none focus:border-red-500"
          />
          <button
            onClick={() => loadList(selectedCategory, searchName)}
            className="px-4 py-2 bg-[#0f3460] text-white text-sm rounded-lg hover:bg-[#1a4a80] transition-colors"
          >
            Search
          </button>
          <span className="text-xs text-gray-500 ml-auto">{objects.length} objects</span>
        </div>

        {/* Table */}
        {listLoading ? (
          <div className="flex-1 flex items-center justify-center text-gray-400">
            <div className="text-center">
              <div className="animate-spin inline-block w-6 h-6 border-2 border-gray-600 border-t-red-500 rounded-full mb-3" />
              <p className="text-sm">Loading...</p>
            </div>
          </div>
        ) : (
          <div className="flex-1 overflow-auto">
            <table className="w-full text-sm border-collapse">
              <thead className="sticky top-0 z-10">
                <tr>
                  <th className="bg-[#16213e] text-left px-4 py-2.5 text-xs font-medium text-gray-300 border-b border-[#1e2d50]">Name</th>
                  <th className="bg-[#16213e] text-left px-4 py-2.5 text-xs font-medium text-gray-300 border-b border-[#1e2d50]">Owner</th>
                  <th className="bg-[#16213e] text-left px-4 py-2.5 text-xs font-medium text-gray-300 border-b border-[#1e2d50]">Modified</th>
                  <th className="bg-[#16213e] text-left px-4 py-2.5 text-xs font-medium text-gray-300 border-b border-[#1e2d50]">ID</th>
                </tr>
              </thead>
              <tbody>
                {objects.map(obj => (
                  <tr
                    key={obj.id}
                    onClick={() => openLineage(obj)}
                    className="hover:bg-[#16213e]/50 transition-colors cursor-pointer"
                  >
                    <td className="px-4 py-2.5 text-gray-200 border-b border-[#1a1a2e]">{obj.name}</td>
                    <td className="px-4 py-2.5 text-gray-400 border-b border-[#1a1a2e]">{obj.owner || '—'}</td>
                    <td className="px-4 py-2.5 text-gray-400 border-b border-[#1a1a2e] whitespace-nowrap">
                      {obj.dateModified ? new Date(obj.dateModified).toLocaleDateString() : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-gray-600 border-b border-[#1a1a2e] font-mono text-xs">{obj.id}</td>
                  </tr>
                ))}
                {objects.length === 0 && (
                  <tr><td colSpan={4} className="px-4 py-8 text-center text-gray-600">No objects found</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  }

  // ===== Render: Lineage tree node =====
  function toggleNode(node: LineageNode) {
    node.expanded = !node.expanded;
    setLineageRoot({ ...lineageRoot! });
  }

  function renderNode(node: LineageNode, depth = 0) {
    const isGroup = node.id.startsWith('group-');
    const hasChildren = node.children.length > 0;
    const typeLabel = TYPE_LABELS[node.type] || `Type ${node.type}`;

    return (
      <div key={node.id + '-' + depth} className="mb-0.5">
        <div
          className="flex items-center gap-2 py-1.5 px-3 rounded-lg hover:bg-[#1a2a4a]/50 transition-colors"
          style={{ marginLeft: depth * 20 }}
        >
          {/* Expand/collapse toggle */}
          {hasChildren ? (
            <button
              onClick={() => isGroup ? toggleNode(node) : expandNode(node)}
              className="text-gray-400 hover:text-white text-xs w-5 h-5 flex items-center justify-center shrink-0"
            >
              {node.loading ? (
                <div className="animate-spin w-3 h-3 border border-gray-600 border-t-blue-400 rounded-full" />
              ) : node.expanded ? '▼' : '▶'}
            </button>
          ) : (
            <span className="w-5 h-5 flex items-center justify-center text-gray-700 text-xs shrink-0">
              {depth === 0 ? '◆' : '•'}
            </span>
          )}

          {/* Group nodes: bold label, no badge/ID */}
          {isGroup ? (
            <span className="text-xs font-semibold text-gray-300">{node.name}</span>
          ) : (
            <>
              <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded border ${
                node.type === 3 ? 'bg-blue-900/30 border-blue-800 text-blue-300' :
                node.type === 21 ? 'bg-green-900/30 border-green-800 text-green-300' :
                node.type === 10 ? 'bg-orange-900/30 border-orange-800 text-orange-300' :
                node.type === 4 ? 'bg-red-900/30 border-red-800 text-red-300' :
                node.type === 7 ? 'bg-purple-900/30 border-purple-800 text-purple-300' :
                'bg-gray-800/30 border-gray-700 text-gray-400'
              }`}>
                {typeLabel}
              </span>
              <span className="text-sm text-gray-200">{node.name}</span>
              <span className="text-[10px] text-gray-600 font-mono ml-2">{node.id}</span>
            </>
          )}

          {node.error && (
            <span className="text-xs text-red-400 ml-2">{node.error}</span>
          )}
        </div>

        {/* Children */}
        {node.expanded && node.children.map(child => renderNode(child, depth + 1))}
      </div>
    );
  }

  // ===== Render: Lineage =====
  function renderLineage() {
    return (
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="flex-1 overflow-auto p-6">
          {lineageLoading ? (
            <div className="flex items-center justify-center py-12 text-gray-400">
              <div className="text-center">
                <div className="animate-spin inline-block w-6 h-6 border-2 border-gray-600 border-t-red-500 rounded-full mb-3" />
                <p className="text-sm">Loading lineage...</p>
              </div>
            </div>
          ) : (
            <div className="space-y-6">
              {/* Top row: Dependency Tree */}
              <div>
                <h3 className="text-xs text-gray-400 uppercase tracking-wider font-semibold mb-3">
                  Dependency Tree
                  {selectedObject && <span className="text-gray-600 ml-2">({selectedObject.name})</span>}
                </h3>
                <div className="bg-[#16213e] border border-[#1e2d50] rounded-xl p-4">
                  {lineageRoot ? renderNode(lineageRoot) : (
                    <p className="text-sm text-gray-600">No lineage data</p>
                  )}
                </div>
              </div>

              {/* SQL Source */}
              {lineageRoot?.sql && (
                <div>
                  <h3 className="text-xs text-yellow-400 uppercase tracking-wider font-semibold mb-3">
                    SQL Source
                  </h3>
                  <div className="bg-[#16213e] border border-[#1e2d50] rounded-xl p-4 max-h-[40vh] overflow-auto">
                    <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap">
                      {lineageRoot.sql}
                    </pre>
                  </div>
                </div>
              )}

              {/* Bottom row: Object Info + Object Definition side by side */}
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <div>
                  <h3 className="text-xs text-gray-400 uppercase tracking-wider font-semibold mb-3">
                    Object Info
                  </h3>
                  <div className="bg-[#16213e] border border-[#1e2d50] rounded-xl p-4 max-h-[50vh] overflow-auto">
                    {objectInfoJson ? (
                      <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap break-words">
                        {JSON.stringify(objectInfoJson, null, 2)}
                      </pre>
                    ) : (
                      <p className="text-sm text-gray-600">Not available</p>
                    )}
                  </div>
                </div>

                <div>
                  <h3 className="text-xs text-gray-400 uppercase tracking-wider font-semibold mb-3">
                    Object Definition
                  </h3>
                  <div className="bg-[#16213e] border border-[#1e2d50] rounded-xl p-4 max-h-[50vh] overflow-auto">
                    {objectDefJson ? (
                      <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap break-words">
                        {JSON.stringify(objectDefJson, null, 2)}
                      </pre>
                    ) : (
                      <p className="text-sm text-gray-600">Not available</p>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ===== Main render =====
  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 py-4 border-b border-[#1e2d50] bg-[#16213e] flex items-center gap-4">
        {subView !== 'overview' && (
          <button
            onClick={() => {
              if (subView === 'lineage' && selectedCategory) {
                goBackToList();
              } else {
                setSubView('overview');
              }
            }}
            className="text-gray-400 hover:text-white transition-colors"
          >
            <VscChevronLeft className="text-lg" />
          </button>
        )}
        {renderBreadcrumb()}
      </div>

      {subView === 'overview' && renderOverview()}
      {subView === 'list' && renderList()}
      {subView === 'lineage' && renderLineage()}
    </div>
  );
}
