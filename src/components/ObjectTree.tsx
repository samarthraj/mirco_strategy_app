import { useState, useEffect, useMemo, useCallback } from 'react';
import {
  VscChevronDown, VscChevronRight, VscSearch, VscClose,
} from 'react-icons/vsc';
import { useApp } from '../context/AppContext';

// ── Types ──

interface InventoryObject {
  id: string;
  name: string;
  type: number;
  subType: string | null;
  description: string | null;
  dateCreated: string | null;
  dateModified: string | null;
  owner: string | null;
  category: string;
  errors: Record<string, string>;
  folderPath?: string;
  definition?: { id: string; name: string; subType: string; dateCreated: string; dateModified: string; versionId: string };
  filter?: { text: string | null; tree: any };
  attributes?: { id: string; name: string; subType: string }[];
  metrics?: { id: string; name: string; subType: string }[];
  sql?: string;
  sqlHash?: string;
  sourceTables?: string[];
}

interface SlimObject {
  id: string;
  name: string;
  category: string;
  type: number;
  owner: string | null;
  dateModified: string | null;
}

interface RationalizationData {
  objects: Record<string, SlimObject>;
  dependentsOf: Record<string, string[]>;
  dependenciesOf: Record<string, string[]>;
}

const CATEGORIES = [
  'reports', 'documents', 'cubes', 'metrics', 'filters',
  'prompts', 'attributes', 'facts', 'tables',
];

const CATEGORY_ICONS: Record<string, string> = {
  reports: '📊', documents: '📄', cubes: '🧊', metrics: '📐',
  filters: '🔍', prompts: '❓', attributes: '🏷️', facts: '📏', tables: '🗃️',
};

const CATEGORY_COLORS: Record<string, string> = {
  reports: 'text-blue-300', documents: 'text-purple-300', cubes: 'text-green-300',
  metrics: 'text-yellow-300', filters: 'text-orange-300', prompts: 'text-pink-300',
  attributes: 'text-cyan-300', facts: 'text-teal-300', tables: 'text-indigo-300',
};

function CategoryBadge({ category }: { category: string }) {
  const colors: Record<string, string> = {
    reports: 'bg-blue-500/20 text-blue-300', documents: 'bg-purple-500/20 text-purple-300',
    cubes: 'bg-green-500/20 text-green-300', metrics: 'bg-yellow-500/20 text-yellow-300',
    filters: 'bg-orange-500/20 text-orange-300', prompts: 'bg-pink-500/20 text-pink-300',
    attributes: 'bg-cyan-500/20 text-cyan-300', facts: 'bg-teal-500/20 text-teal-300',
    tables: 'bg-indigo-500/20 text-indigo-300',
  };
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full whitespace-nowrap ${colors[category] || 'bg-gray-500/20 text-gray-300'}`}>
      {category}
    </span>
  );
}

// ── Detail Panel ──

function DetailPanel({ obj, onClose }: { obj: InventoryObject; onClose: () => void }) {
  const [activeSection, setActiveSection] = useState<string>('info');

  const hasSql = !!obj.sql;
  const hasFilter = !!obj.filter?.text;
  const hasAttrs = (obj.attributes?.length || 0) > 0;
  const hasMetrics = (obj.metrics?.length || 0) > 0;
  const hasTables = (obj.sourceTables?.length || 0) > 0;
  const hasErrors = Object.keys(obj.errors || {}).length > 0;

  const sections = [
    { key: 'info', label: 'Info' },
    ...(hasSql ? [{ key: 'sql', label: 'SQL' }] : []),
    ...(hasFilter ? [{ key: 'filter', label: 'Filter' }] : []),
    ...(hasAttrs || hasMetrics ? [{ key: 'columns', label: 'Columns' }] : []),
    ...(hasTables ? [{ key: 'tables', label: 'Source Tables' }] : []),
    ...(hasErrors ? [{ key: 'errors', label: 'Errors' }] : []),
  ];

  return (
    <div className="w-[500px] bg-[#16213e] border-l border-[#1e2d50] flex flex-col overflow-hidden shrink-0">
      <div className="p-3 border-b border-[#1e2d50] flex items-center gap-2">
        <span className="text-lg">{CATEGORY_ICONS[obj.category] || '📦'}</span>
        <div className="flex-1 min-w-0">
          <div className="text-white font-medium text-sm truncate" title={obj.name}>{obj.name}</div>
          <div className="flex items-center gap-2 mt-0.5">
            <CategoryBadge category={obj.category} />
            <span className="text-xs text-gray-500 font-mono truncate">{obj.id}</span>
          </div>
        </div>
        <button onClick={onClose} className="text-gray-400 hover:text-white p-1 shrink-0"><VscClose /></button>
      </div>

      <div className="flex border-b border-[#1e2d50] overflow-x-auto">
        {sections.map(s => (
          <button key={s.key} onClick={() => setActiveSection(s.key)}
            className={`px-3 py-2 text-xs whitespace-nowrap border-b-2 transition-colors ${
              activeSection === s.key ? 'text-white border-red-500' : 'text-gray-400 border-transparent hover:text-white'
            }`}>{s.label}</button>
        ))}
      </div>

      <div className="flex-1 overflow-auto p-3">
        {activeSection === 'info' && (
          <div className="space-y-3">
            <InfoRow label="Name" value={obj.name} />
            <InfoRow label="ID" value={obj.id} mono />
            <InfoRow label="Category" value={obj.category} />
            {obj.folderPath && <InfoRow label="Folder" value={obj.folderPath} />}
            <InfoRow label="Type" value={`${obj.type}${obj.definition?.subType ? ` / ${obj.definition.subType}` : ''}`} />
            <InfoRow label="Description" value={obj.description || '-'} />
            <InfoRow label="Owner" value={obj.owner || '-'} />
            <InfoRow label="Created" value={obj.dateCreated?.split('T')[0] || '-'} />
            <InfoRow label="Modified" value={obj.dateModified?.split('T')[0] || '-'} />
            {obj.definition?.versionId && <InfoRow label="Version" value={obj.definition.versionId} mono />}
            {obj.sqlHash && <InfoRow label="SQL Hash" value={obj.sqlHash} mono />}
          </div>
        )}
        {activeSection === 'sql' && obj.sql && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-gray-400">Generated SQL</span>
              <button onClick={() => navigator.clipboard.writeText(obj.sql!)} className="text-xs text-blue-400 hover:text-blue-300">Copy</button>
            </div>
            <pre className="bg-[#0f0f1a] p-3 rounded text-xs text-green-300 font-mono whitespace-pre-wrap break-all overflow-auto max-h-[600px]">{obj.sql}</pre>
          </div>
        )}
        {activeSection === 'filter' && obj.filter && (
          <div>
            {obj.filter.text && (
              <div className="mb-4">
                <div className="text-xs text-gray-400 mb-1">Filter Expression</div>
                <div className="bg-[#0f0f1a] p-3 rounded text-sm text-orange-300">{obj.filter.text}</div>
              </div>
            )}
            {obj.filter.tree && (
              <div>
                <div className="text-xs text-gray-400 mb-1">Filter Tree</div>
                <pre className="bg-[#0f0f1a] p-3 rounded text-xs text-gray-400 font-mono whitespace-pre-wrap break-all overflow-auto max-h-[400px]">{JSON.stringify(obj.filter.tree, null, 2)}</pre>
              </div>
            )}
          </div>
        )}
        {activeSection === 'columns' && (
          <div className="space-y-4">
            {hasAttrs && (
              <div>
                <div className="text-xs text-gray-400 mb-2">Attributes ({obj.attributes!.length})</div>
                <div className="space-y-1">{obj.attributes!.map((a, i) => (
                  <div key={i} className="flex items-center gap-2 text-sm bg-[#0f0f1a] px-3 py-1.5 rounded">
                    <span className="text-cyan-400">{a.name}</span>
                    <span className="text-gray-600 text-xs font-mono ml-auto">{a.id}</span>
                  </div>
                ))}</div>
              </div>
            )}
            {hasMetrics && (
              <div>
                <div className="text-xs text-gray-400 mb-2">Metrics ({obj.metrics!.length})</div>
                <div className="space-y-1">{obj.metrics!.map((m, i) => (
                  <div key={i} className="flex items-center gap-2 text-sm bg-[#0f0f1a] px-3 py-1.5 rounded">
                    <span className="text-yellow-400">{m.name}</span>
                    <span className="text-gray-600 text-xs font-mono ml-auto">{m.id}</span>
                  </div>
                ))}</div>
              </div>
            )}
          </div>
        )}
        {activeSection === 'tables' && obj.sourceTables && (
          <div>
            <div className="text-xs text-gray-400 mb-2">Source Tables ({obj.sourceTables.length})</div>
            <div className="space-y-1">{obj.sourceTables.map((t, i) => (
              <div key={i} className="bg-[#0f0f1a] px-3 py-1.5 rounded text-sm text-indigo-300 font-mono">{t}</div>
            ))}</div>
          </div>
        )}
        {activeSection === 'errors' && obj.errors && (
          <div className="space-y-3">{Object.entries(obj.errors).map(([key, msg]) => (
            <div key={key}>
              <div className="text-xs text-red-400 font-semibold mb-1">{key}</div>
              <pre className="bg-[#0f0f1a] p-3 rounded text-xs text-red-300 font-mono whitespace-pre-wrap break-all">{msg}</pre>
            </div>
          ))}</div>
        )}
      </div>
    </div>
  );
}

function InfoRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-3">
      <span className="text-xs text-gray-500 w-20 shrink-0">{label}</span>
      <span className={`text-sm ${mono ? 'font-mono text-gray-400 text-xs break-all' : 'text-gray-200'}`}>{value}</span>
    </div>
  );
}

// ── Dependency Tree Node (recursive) ──

function DepTreeNode({
  objectId, depth, direction, rationalization, inventoryLookup, selectedId,
  onSelect, expandedIds, toggleExpand,
}: {
  objectId: string;
  depth: number;
  direction: 'down' | 'up'; // down = what this object depends on, up = what uses this
  rationalization: RationalizationData;
  inventoryLookup: Record<string, InventoryObject>;
  selectedId: string | null;
  onSelect: (id: string) => void;
  expandedIds: Set<string>;
  toggleExpand: (id: string) => void;
}) {
  const obj = rationalization.objects[objectId];
  if (!obj) return null;

  const childIds = direction === 'down'
    ? rationalization.dependenciesOf[objectId] || []
    : rationalization.dependentsOf[objectId] || [];

  const uniqueChildIds = [...new Set(childIds)];
  const hasChildren = uniqueChildIds.length > 0;
  const nodeKey = `${direction}:${objectId}`;
  const expanded = expandedIds.has(nodeKey);

  // Group children by category
  const groupedChildren = useMemo(() => {
    if (!expanded) return {};
    const groups: Record<string, string[]> = {};
    for (const cid of uniqueChildIds) {
      const child = rationalization.objects[cid];
      const cat = child?.category || 'unknown';
      (groups[cat] ||= []).push(cid);
    }
    return groups;
  }, [expanded, uniqueChildIds, rationalization.objects]);

  const isSelected = selectedId === objectId;
  const indent = depth * 20;

  return (
    <div>
      <div
        className={`flex items-center gap-1.5 py-1 pr-2 cursor-pointer transition-colors ${
          isSelected ? 'bg-[#0f3460]' : 'hover:bg-[#1a2a4a]'
        }`}
        style={{ paddingLeft: `${12 + indent}px` }}
      >
        {/* Expand/collapse toggle */}
        {hasChildren ? (
          <button onClick={() => toggleExpand(nodeKey)} className="text-gray-400 hover:text-white shrink-0 w-4">
            {expanded ? <VscChevronDown className="text-xs" /> : <VscChevronRight className="text-xs" />}
          </button>
        ) : (
          <span className="w-4 shrink-0" />
        )}

        {/* Object info */}
        <button onClick={() => onSelect(objectId)} className="flex items-center gap-1.5 min-w-0 flex-1 text-left">
          <span className="text-xs shrink-0">{CATEGORY_ICONS[obj.category] || '📦'}</span>
          <span className={`text-sm truncate ${isSelected ? 'text-white font-medium' : CATEGORY_COLORS[obj.category] || 'text-gray-300'}`}>
            {obj.name}
          </span>
          {hasChildren && (
            <span className="text-[10px] text-gray-600 shrink-0 ml-1">({uniqueChildIds.length})</span>
          )}
        </button>
      </div>

      {/* Expanded children grouped by category */}
      {expanded && Object.entries(groupedChildren)
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([cat, ids]) => (
          <div key={cat}>
            {Object.keys(groupedChildren).length > 1 && (
              <div className="text-[10px] text-gray-600 uppercase tracking-wider"
                   style={{ paddingLeft: `${32 + indent}px`, paddingTop: '4px' }}>
                {cat} ({ids.length})
              </div>
            )}
            {ids.slice(0, 100).map(cid => (
              <DepTreeNode
                key={cid}
                objectId={cid}
                depth={depth + 1}
                direction={direction}
                rationalization={rationalization}
                inventoryLookup={inventoryLookup}
                selectedId={selectedId}
                onSelect={onSelect}
                expandedIds={expandedIds}
                toggleExpand={toggleExpand}
              />
            ))}
            {ids.length > 100 && (
              <div className="text-xs text-gray-600" style={{ paddingLeft: `${32 + indent}px` }}>
                ...and {ids.length - 100} more
              </div>
            )}
          </div>
        ))
      }
    </div>
  );
}

// ── Root Tree: shows consumer objects with their dependencies ──

function DependencyTree({
  category, objects, rationalization, inventoryLookup, searchQuery, selectedId,
  onSelect, expandedIds, toggleExpand,
}: {
  category: string;
  objects: string[];
  rationalization: RationalizationData;
  inventoryLookup: Record<string, InventoryObject>;
  searchQuery: string;
  selectedId: string | null;
  onSelect: (id: string) => void;
  expandedIds: Set<string>;
  toggleExpand: (id: string) => void;
}) {
  const [catExpanded, setCatExpanded] = useState(false);

  const filtered = useMemo(() => {
    if (!searchQuery) return objects;
    const q = searchQuery.toLowerCase();
    return objects.filter(id => {
      const obj = rationalization.objects[id];
      return obj && (obj.name.toLowerCase().includes(q) || id.toLowerCase().includes(q));
    });
  }, [objects, searchQuery, rationalization.objects]);

  useEffect(() => {
    if (searchQuery && filtered.length > 0) setCatExpanded(true);
  }, [searchQuery, filtered.length]);

  if (searchQuery && filtered.length === 0) return null;

  const count = searchQuery ? `${filtered.length}/${objects.length}` : `${objects.length}`;

  return (
    <div>
      <button
        onClick={() => setCatExpanded(!catExpanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-[#1a2a4a] transition-colors border-b border-[#1e2d50]/30"
      >
        {catExpanded ? <VscChevronDown className="text-gray-400" /> : <VscChevronRight className="text-gray-400" />}
        <span className="text-base">{CATEGORY_ICONS[category] || '📦'}</span>
        <span className={`text-sm font-medium ${CATEGORY_COLORS[category] || 'text-gray-300'}`}>{category}</span>
        <span className="text-xs text-gray-500 ml-auto">{count}</span>
      </button>

      {catExpanded && (
        <div className="border-l-2 border-[#1e2d50] ml-4">
          {filtered.slice(0, 300).map(id => (
            <DepTreeNode
              key={id}
              objectId={id}
              depth={0}
              direction="down"
              rationalization={rationalization}
              inventoryLookup={inventoryLookup}
              selectedId={selectedId}
              onSelect={onSelect}
              expandedIds={expandedIds}
              toggleExpand={toggleExpand}
            />
          ))}
          {filtered.length > 300 && (
            <div className="px-4 py-1.5 text-xs text-gray-600">...and {filtered.length - 300} more (refine search)</div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main Component ──

interface RunInfo {
  id: string;
  label: string;
  timestamp: string;
  totalObjects: number;
  description?: string;
}

export default function ObjectTree() {
  const { currentProject } = useApp();
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>('');
  const [inventoryLookup, setInventoryLookup] = useState<Record<string, InventoryObject>>({});
  const [rationalization, setRationalization] = useState<RationalizationData | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadProgress, setLoadProgress] = useState('');
  const [noData, setNoData] = useState(false);
  const [search, setSearch] = useState('');
  const [selectedObj, setSelectedObj] = useState<InventoryObject | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [viewMode, setViewMode] = useState<'depends-on' | 'used-by'>('depends-on');

  const projectId = currentProject?.id;

  // Load runs index
  useEffect(() => {
    if (!projectId) return;
    setLoading(true);
    setNoData(false);
    setRuns([]);
    setSelectedRunId('');
    fetch(`/projects/${projectId}/runs/runs.json`)
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then((data: RunInfo[]) => {
        setRuns(data);
        if (data.length > 0) setSelectedRunId(data[data.length - 1].id);
        else { setNoData(true); setLoading(false); }
      })
      .catch(() => {
        // Fallback to old single-file path
        setSelectedRunId('__legacy__');
      });
  }, [projectId]);

  // Load data for selected run
  useEffect(() => {
    if (!projectId || !selectedRunId) return;

    setLoading(true);
    setNoData(false);
    setRationalization(null);
    setInventoryLookup({});
    setSelectedObj(null);
    setExpandedIds(new Set());

    async function loadAll() {
      const basePath = selectedRunId === '__legacy__'
        ? `/projects/${projectId}`
        : `/projects/${projectId}/runs/${selectedRunId}`;

      setLoadProgress('Loading dependency graph...');
      try {
        const resp = await fetch(`${basePath}/rationalization_report.json`);
        if (resp.ok) {
          const data = await resp.json();
          setRationalization({
            objects: data.objects || {},
            dependentsOf: data.dependentsOf || {},
            dependenciesOf: data.dependenciesOf || {},
          });
        } else {
          setNoData(true);
          setLoading(false);
          return;
        }
      } catch {
        setNoData(true);
        setLoading(false);
        return;
      }

      const lookup: Record<string, InventoryObject> = {};
      for (const cat of CATEGORIES) {
        setLoadProgress(`Loading ${cat}...`);
        try {
          const resp = await fetch(`${basePath}/inventory/${cat}.json`);
          if (resp.ok) {
            const data = await resp.json();
            if (Array.isArray(data)) {
              for (const obj of data) {
                if (obj.id) lookup[obj.id] = obj;
              }
            }
          }
        } catch { /* skip */ }
      }
      setInventoryLookup(lookup);
      setLoading(false);
    }
    loadAll();
  }, [projectId, selectedRunId]);

  // Build category -> object IDs for tree roots
  const categoryObjectIds = useMemo(() => {
    if (!rationalization) return {};
    const groups: Record<string, string[]> = {};
    for (const [id, obj] of Object.entries(rationalization.objects)) {
      (groups[obj.category] ||= []).push(id);
    }
    // Sort objects within each category by name
    for (const ids of Object.values(groups)) {
      ids.sort((a, b) => {
        const na = rationalization.objects[a]?.name || '';
        const nb = rationalization.objects[b]?.name || '';
        return na.localeCompare(nb);
      });
    }
    return groups;
  }, [rationalization]);

  const totalObjects = useMemo(
    () => rationalization ? Object.keys(rationalization.objects).length : 0,
    [rationalization]
  );

  const toggleExpand = useCallback((nodeKey: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(nodeKey)) next.delete(nodeKey);
      else next.add(nodeKey);
      return next;
    });
  }, []);

  const handleSelect = useCallback((id: string) => {
    // Use full inventory object for detail (has SQL etc), fall back to slim
    const full = inventoryLookup[id];
    if (full) {
      setSelectedObj(full);
    } else if (rationalization?.objects[id]) {
      const slim = rationalization.objects[id];
      setSelectedObj({
        id: slim.id, name: slim.name, category: slim.category, type: slim.type,
        owner: slim.owner, dateModified: slim.dateModified, subType: null,
        description: null, dateCreated: null, errors: {},
      });
    }
  }, [inventoryLookup, rationalization]);

  if (loading) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-gray-500">
        <div className="text-lg mb-2">Loading object tree...</div>
        <div className="text-sm">{loadProgress}</div>
      </div>
    );
  }

  if (!rationalization || noData) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-gray-500 p-6">
        <p className="text-lg mb-1">No rationalization data for this project</p>
        <p className="text-sm text-gray-600 mb-2">
          {currentProject ? `Project: ${currentProject.name}` : 'No project selected'}
        </p>
        <p className="text-xs text-gray-600">
          Run: <code className="bg-[#16213e] px-2 py-0.5 rounded text-gray-300">python mstr_project_rationalize.py --project-name "{currentProject?.name}"</code>
        </p>
        <p className="text-xs text-gray-600 mt-1">
          Then copy output to <code className="bg-[#16213e] px-2 py-0.5 rounded text-gray-300">public/projects/{'{projectId}'}/</code>
        </p>
      </div>
    );
  }

  // Order categories: consumers first, then supporting, then schema
  const categoryOrder = ['reports', 'documents', 'cubes', 'metrics', 'prompts', 'filters', 'attributes', 'facts', 'tables'];
  const orderedCategories = categoryOrder.filter(c => (categoryObjectIds[c]?.length || 0) > 0);

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="p-4 border-b border-[#1e2d50] bg-[#16213e]">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold text-white mb-1">Object Dependency Tree</h2>
            <p className="text-xs text-gray-400">
              {totalObjects.toLocaleString()} objects — expand any object to see its dependencies. Search by name or ID.
            </p>
          </div>
          {runs.length > 1 && (
            <select
              value={selectedRunId}
              onChange={(e) => setSelectedRunId(e.target.value)}
              className="bg-[#0f0f1a] border border-[#1e2d50] text-gray-300 text-sm rounded px-3 py-2"
            >
              {runs.map(r => (
                <option key={r.id} value={r.id}>
                  {r.label} ({r.timestamp.split('T')[0]})
                </option>
              ))}
            </select>
          )}
          {runs.length === 1 && (
            <span className="text-xs text-gray-500">Run: {runs[0].label}</span>
          )}
        </div>
      </div>

      {/* Search + view mode */}
      <div className="p-3 border-b border-[#1e2d50] bg-[#16213e]/50 flex gap-3">
        <div className="flex-1 relative">
          <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            placeholder="Search by name or object ID..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-8 py-2 bg-[#0f0f1a] border border-[#1e2d50] rounded text-sm text-gray-300 placeholder-gray-600"
          />
          {search && (
            <button onClick={() => setSearch('')} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-white">
              <VscClose className="text-sm" />
            </button>
          )}
        </div>
        <div className="flex bg-[#0f0f1a] border border-[#1e2d50] rounded overflow-hidden">
          <button
            onClick={() => setViewMode('depends-on')}
            className={`px-3 py-2 text-xs ${viewMode === 'depends-on' ? 'bg-[#0f3460] text-white' : 'text-gray-400 hover:text-white'}`}
          >
            Depends On
          </button>
          <button
            onClick={() => setViewMode('used-by')}
            className={`px-3 py-2 text-xs ${viewMode === 'used-by' ? 'bg-[#0f3460] text-white' : 'text-gray-400 hover:text-white'}`}
          >
            Used By
          </button>
        </div>
      </div>

      {/* Tree + Detail */}
      <div className="flex-1 flex overflow-hidden">
        {/* Tree panel */}
        <div className="flex-1 overflow-auto">
          {/* Swap dependency direction based on view mode */}
          {orderedCategories.map(category => (
            <DependencyTree
              key={`${category}-${viewMode}`}
              category={category}
              objects={categoryObjectIds[category] || []}
              rationalization={
                viewMode === 'depends-on'
                  ? rationalization
                  : {
                      ...rationalization,
                      // Swap: show dependents as children instead of dependencies
                      dependenciesOf: rationalization.dependentsOf,
                      dependentsOf: rationalization.dependenciesOf,
                    }
              }
              inventoryLookup={inventoryLookup}
              searchQuery={search}
              selectedId={selectedObj?.id || null}
              onSelect={handleSelect}
              expandedIds={expandedIds}
              toggleExpand={toggleExpand}
            />
          ))}
        </div>

        {/* Detail panel */}
        {selectedObj && (
          <DetailPanel obj={selectedObj} onClose={() => setSelectedObj(null)} />
        )}
      </div>
    </div>
  );
}
