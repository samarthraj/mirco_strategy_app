import { useState, useEffect, useMemo, useCallback } from 'react';
import { VscWarning, VscCopy, VscHistory, VscGraphLine, VscSymbolMethod, VscChevronDown, VscChevronRight, VscClose, VscSearch, VscArrowLeft } from 'react-icons/vsc';
import { useApp } from '../context/AppContext';

// ── Types ──────────────────────────────────────────────────────────────────

interface Summary {
  totalObjects: number;
  byCategory: Record<string, number>;
  totalEdges: number;
  orphanCount: number;
  duplicateSqlGroupCount: number;
  duplicateSqlObjectCount: number;
  highImpactCount: number;
  staleObjectCount: number;
  unusedMetricCount: number;
  errorCount: number;
}

interface ObjectInfo {
  id: string;
  name: string;
  category: string;
  type: number;
  owner: string | null;
  dateModified: string | null;
  folderPath?: string;
}

interface DuplicateSqlGroup {
  sqlHash: string;
  normalizedSqlPreview: string;
  count: number;
  objects: { id: string; name: string; category: string; path: string | null; sql_preview: string }[];
}

interface StaleItem {
  id: string; name: string; type: number; category: string;
  owner: string; path: string | null; dateModified: string; daysSinceModified: number;
}

interface UnusedMetric {
  id: string; name: string; owner: string; path: string | null; dateModified: string;
}

interface HighImpactItem {
  id: string; name: string; type: number; category: string;
  path: string | null; dependentCount: number;
}

interface OrphanItem {
  id: string; name: string; type: number; subType: number | null; category: string;
  owner: string; path: string | null; dateModified: string | null;
}

interface ErrorItem {
  id: string;
  name: string;
  category: string;
  errorType: string;
  message: string;
}

interface RationalizationReport {
  summary: Summary;
  orphans: OrphanItem[];
  duplicateSql: DuplicateSqlGroup[];
  highImpact: HighImpactItem[];
  staleObjects: StaleItem[];
  unusedMetrics: UnusedMetric[];
  errors: ErrorItem[];
  objects: Record<string, ObjectInfo>;
  dependentsOf: Record<string, string[]>;
  dependenciesOf: Record<string, string[]>;
}

type Tab = 'overview' | 'dependencies' | 'duplicates' | 'orphans' | 'stale' | 'unused-metrics' | 'errors';

// ── Shared helpers ─────────────────────────────────────────────────────────

const CATEGORY_COLORS: Record<string, string> = {
  reports: 'bg-blue-500/20 text-blue-300',
  documents: 'bg-purple-500/20 text-purple-300',
  cubes: 'bg-green-500/20 text-green-300',
  metrics: 'bg-yellow-500/20 text-yellow-300',
  filters: 'bg-orange-500/20 text-orange-300',
  prompts: 'bg-pink-500/20 text-pink-300',
  attributes: 'bg-cyan-500/20 text-cyan-300',
  facts: 'bg-teal-500/20 text-teal-300',
  tables: 'bg-indigo-500/20 text-indigo-300',
  security_filters: 'bg-red-500/20 text-red-300',
  custom_groups: 'bg-amber-500/20 text-amber-300',
};

function CategoryBadge({ category }: { category: string }) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full whitespace-nowrap ${CATEGORY_COLORS[category] || 'bg-gray-500/20 text-gray-300'}`}>
      {category}
    </span>
  );
}

function ObjectLink({ obj, onClick }: { obj: ObjectInfo | undefined; onClick: (id: string) => void }) {
  if (!obj) return <span className="text-gray-600">Unknown</span>;
  return (
    <button
      onClick={() => onClick(obj.id)}
      className="text-blue-400 hover:text-blue-300 hover:underline text-left truncate"
    >
      {obj.name}
    </button>
  );
}

function StatCard({ label, value, icon: Icon, color }: { label: string; value: number | string; icon: typeof VscWarning; color: string }) {
  return (
    <div className="bg-[#16213e] border border-[#1e2d50] rounded-lg p-4 flex items-center gap-4">
      <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${color}`}>
        <Icon className="text-lg" />
      </div>
      <div>
        <div className="text-2xl font-bold text-white">{typeof value === 'number' ? value.toLocaleString() : value}</div>
        <div className="text-xs text-gray-400">{label}</div>
      </div>
    </div>
  );
}

// ── Dependency Detail Panel (slide-over) ───────────────────────────────────

function DependencyDetailPanel({
  objectId, objects, dependentsOf, dependenciesOf, onNavigate, onClose,
}: {
  objectId: string;
  objects: Record<string, ObjectInfo>;
  dependentsOf: Record<string, string[]>;
  dependenciesOf: Record<string, string[]>;
  onNavigate: (id: string) => void;
  onClose: () => void;
}) {
  const obj = objects[objectId];
  const dependents = (dependentsOf[objectId] || []).map(id => objects[id]).filter(Boolean);
  const dependencies = (dependenciesOf[objectId] || []).map(id => objects[id]).filter(Boolean);
  const [history, setHistory] = useState<string[]>([]);

  const navigate = useCallback((id: string) => {
    setHistory(prev => [...prev, objectId]);
    onNavigate(id);
  }, [objectId, onNavigate]);

  const goBack = useCallback(() => {
    const prev = history[history.length - 1];
    if (prev) {
      setHistory(h => h.slice(0, -1));
      onNavigate(prev);
    }
  }, [history, onNavigate]);

  if (!obj) return null;

  // Group by category
  const groupByCategory = (items: ObjectInfo[]) => {
    const grouped: Record<string, ObjectInfo[]> = {};
    for (const item of items) {
      (grouped[item.category] ||= []).push(item);
    }
    return Object.entries(grouped).sort((a, b) => b[1].length - a[1].length);
  };

  return (
    <div className="w-[420px] bg-[#16213e] border-l border-[#1e2d50] flex flex-col overflow-hidden shrink-0">
      {/* Header */}
      <div className="p-3 border-b border-[#1e2d50] flex items-center gap-2">
        {history.length > 0 && (
          <button onClick={goBack} className="text-gray-400 hover:text-white p-1">
            <VscArrowLeft />
          </button>
        )}
        <div className="flex-1 min-w-0">
          <div className="text-white font-medium text-sm truncate" title={obj.name}>{obj.name}</div>
          <div className="flex items-center gap-2 mt-1">
            <CategoryBadge category={obj.category} />
            <span className="text-xs text-gray-500 truncate">{obj.id}</span>
          </div>
        </div>
        <button onClick={onClose} className="text-gray-400 hover:text-white p-1 shrink-0">
          <VscClose />
        </button>
      </div>

      {/* Info */}
      <div className="px-3 py-2 border-b border-[#1e2d50] text-xs text-gray-400 space-y-1">
        {obj.folderPath && (
          <div className="text-gray-500 truncate" title={obj.folderPath}>
            <span className="text-gray-300">{obj.folderPath}</span>
          </div>
        )}
        <div className="flex gap-4">
          {obj.owner && <span>Owner: <span className="text-gray-300">{obj.owner}</span></span>}
          {obj.dateModified && <span>Modified: <span className="text-gray-300">{obj.dateModified.split('T')[0]}</span></span>}
        </div>
      </div>

      {/* Dependency lists */}
      <div className="flex-1 overflow-auto">
        {/* Used by (dependents) */}
        <div className="p-3">
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            Used by ({dependents.length})
          </h4>
          {dependents.length === 0 ? (
            <p className="text-xs text-gray-600 italic">No objects depend on this</p>
          ) : (
            <div className="space-y-2">
              {groupByCategory(dependents).map(([cat, items]) => (
                <div key={cat}>
                  <div className="flex items-center gap-2 mb-1">
                    <CategoryBadge category={cat} />
                    <span className="text-xs text-gray-500">{items.length}</span>
                  </div>
                  <div className="pl-2 space-y-0.5">
                    {items.slice(0, 30).map(item => (
                      <div key={item.id} className="text-sm">
                        <ObjectLink obj={item} onClick={navigate} />
                      </div>
                    ))}
                    {items.length > 30 && <div className="text-xs text-gray-600">...and {items.length - 30} more</div>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Depends on (dependencies) */}
        <div className="p-3 border-t border-[#1e2d50]">
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            Depends on ({dependencies.length})
          </h4>
          {dependencies.length === 0 ? (
            <p className="text-xs text-gray-600 italic">No dependencies found</p>
          ) : (
            <div className="space-y-2">
              {groupByCategory(dependencies).map(([cat, items]) => (
                <div key={cat}>
                  <div className="flex items-center gap-2 mb-1">
                    <CategoryBadge category={cat} />
                    <span className="text-xs text-gray-500">{items.length}</span>
                  </div>
                  <div className="pl-2 space-y-0.5">
                    {items.slice(0, 30).map(item => (
                      <div key={item.id} className="text-sm">
                        <ObjectLink obj={item} onClick={navigate} />
                      </div>
                    ))}
                    {items.length > 30 && <div className="text-xs text-gray-600">...and {items.length - 30} more</div>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Dependencies Tab ───────────────────────────────────────────────────────

function DependenciesTab({
  objects, dependentsOf, dependenciesOf, highImpact, onSelect,
}: {
  objects: Record<string, ObjectInfo>;
  dependentsOf: Record<string, string[]>;
  dependenciesOf: Record<string, string[]>;
  highImpact: HighImpactItem[];
  onSelect: (id: string) => void;
}) {
  const [search, setSearch] = useState('');
  const [filterCat, setFilterCat] = useState('all');

  const allObjects = useMemo(() => Object.values(objects), [objects]);
  const categories = useMemo(() => [...new Set(allObjects.map(o => o.category))].sort(), [allObjects]);

  const filtered = useMemo(() => {
    let list = allObjects;
    if (filterCat !== 'all') list = list.filter(o => o.category === filterCat);
    if (search) {
      const q = search.toLowerCase();
      list = list.filter(o => o.name.toLowerCase().includes(q));
    }
    // Sort by dependent count descending
    list.sort((a, b) => (dependentsOf[b.id]?.length || 0) - (dependentsOf[a.id]?.length || 0));
    return list;
  }, [allObjects, filterCat, search, dependentsOf]);

  return (
    <div className="p-4">
      {/* Search + filter */}
      <div className="flex gap-3 mb-4">
        <div className="flex-1 relative">
          <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            placeholder="Search objects..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-3 py-2 bg-[#0f0f1a] border border-[#1e2d50] rounded text-sm text-gray-300 placeholder-gray-600"
          />
        </div>
        <select
          value={filterCat}
          onChange={(e) => setFilterCat(e.target.value)}
          className="bg-[#0f0f1a] border border-[#1e2d50] text-gray-300 text-xs rounded px-2 py-1"
        >
          <option value="all">All categories</option>
          {categories.map(c => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
      </div>

      {/* High impact callout */}
      {!search && filterCat === 'all' && highImpact.length > 0 && (
        <div className="bg-[#16213e] border border-[#1e2d50] rounded-lg p-3 mb-4">
          <h3 className="text-sm font-semibold text-white mb-2">Top High-Impact Objects</h3>
          <div className="space-y-1">
            {highImpact.slice(0, 10).map(h => (
              <div key={h.id} className="flex items-center gap-3 text-sm">
                <span className="text-red-400 font-mono text-xs w-12 text-right">{h.dependentCount}</span>
                <CategoryBadge category={h.category} />
                <button onClick={() => onSelect(h.id)} className="text-blue-400 hover:text-blue-300 hover:underline truncate">
                  {h.name}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Object table */}
      <div className="bg-[#16213e] border border-[#1e2d50] rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[#1e2d50] text-gray-500 text-xs">
              <th className="text-left px-3 py-2 font-medium">Name</th>
              <th className="text-left px-3 py-2 font-medium">Category</th>
              <th className="text-right px-3 py-2 font-medium">Used By</th>
              <th className="text-right px-3 py-2 font-medium">Depends On</th>
              <th className="text-left px-3 py-2 font-medium">Owner</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 200).map(o => {
              const depCount = dependentsOf[o.id]?.length || 0;
              const usesCount = dependenciesOf[o.id]?.length || 0;
              return (
                <tr key={o.id} className="border-b border-[#1e2d50]/50 hover:bg-[#1a2a4a] cursor-pointer" onClick={() => onSelect(o.id)}>
                  <td className="px-3 py-1.5 text-blue-400 hover:text-blue-300">{o.name}</td>
                  <td className="px-3 py-1.5"><CategoryBadge category={o.category} /></td>
                  <td className="px-3 py-1.5 text-right">
                    {depCount > 0 ? <span className="text-green-400 font-mono text-xs">{depCount}</span> : <span className="text-gray-600">0</span>}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    {usesCount > 0 ? <span className="text-cyan-400 font-mono text-xs">{usesCount}</span> : <span className="text-gray-600">0</span>}
                  </td>
                  <td className="px-3 py-1.5 text-gray-400 text-xs">{o.owner || '-'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {filtered.length > 200 && (
          <div className="px-3 py-2 text-xs text-gray-500">Showing 200 of {filtered.length.toLocaleString()} — refine your search</div>
        )}
      </div>
    </div>
  );
}

// ── Inventory Breakdown ────────────────────────────────────────────────────

function InventoryBreakdown({ byCategory }: { byCategory: Record<string, number> }) {
  const sorted = Object.entries(byCategory).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const total = sorted.reduce((s, [, v]) => s + v, 0);

  return (
    <div className="bg-[#16213e] border border-[#1e2d50] rounded-lg p-4">
      <h3 className="text-sm font-semibold text-white mb-3">Inventory Breakdown</h3>
      <div className="space-y-2">
        {sorted.map(([cat, count]) => (
          <div key={cat} className="flex items-center gap-3">
            <CategoryBadge category={cat} />
            <div className="flex-1 h-2 bg-[#0f0f1a] rounded-full overflow-hidden">
              <div className="h-full bg-blue-500/50 rounded-full" style={{ width: `${(count / total) * 100}%` }} />
            </div>
            <span className="text-sm text-gray-300 w-16 text-right">{count.toLocaleString()}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Tab components ─────────────────────────────────────────────────────────

function DuplicateSqlTab({ groups, onSelect }: { groups: DuplicateSqlGroup[]; onSelect: (id: string) => void }) {
  const [expandedHash, setExpandedHash] = useState<string | null>(null);

  if (groups.length === 0) return <div className="p-8 text-center text-gray-500">No duplicate SQL detected.</div>;

  return (
    <div className="space-y-2 p-4">
      <p className="text-sm text-gray-400 mb-4">
        {groups.length} group{groups.length !== 1 ? 's' : ''} of reports/cubes with identical SQL queries.
      </p>
      {groups.map((g) => {
        const expanded = expandedHash === g.sqlHash;
        return (
          <div key={g.sqlHash} className="bg-[#16213e] border border-[#1e2d50] rounded-lg overflow-hidden">
            <button
              onClick={() => setExpandedHash(expanded ? null : g.sqlHash)}
              className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-[#1a2a4a] transition-colors"
            >
              {expanded ? <VscChevronDown className="text-gray-400" /> : <VscChevronRight className="text-gray-400" />}
              <span className="text-red-400 font-bold text-sm">{g.count} duplicates</span>
              <span className="text-gray-400 text-xs truncate flex-1">{g.normalizedSqlPreview}</span>
            </button>
            {expanded && (
              <div className="border-t border-[#1e2d50] px-4 py-3 space-y-2">
                {g.objects.map((obj) => (
                  <div key={obj.id} className="flex items-center gap-3 text-sm">
                    <CategoryBadge category={obj.category} />
                    <button onClick={() => onSelect(obj.id)} className="text-blue-400 hover:text-blue-300 hover:underline">{obj.name}</button>
                    <span className="text-gray-600 text-xs">{obj.id}</span>
                  </div>
                ))}
                <div className="mt-3 p-3 bg-[#0f0f1a] rounded text-xs text-gray-400 font-mono whitespace-pre-wrap break-all">
                  {g.normalizedSqlPreview}...
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function OrphansTab({ orphans, onSelect }: { orphans: OrphanItem[]; onSelect: (id: string) => void }) {
  const [filterCat, setFilterCat] = useState<string>('all');
  const categories = [...new Set(orphans.map((o) => o.category))].sort();
  const filtered = filterCat === 'all' ? orphans : orphans.filter((o) => o.category === filterCat);
  const grouped = filtered.reduce<Record<string, OrphanItem[]>>((acc, o) => {
    (acc[o.category] ||= []).push(o);
    return acc;
  }, {});

  return (
    <div className="p-4">
      <div className="flex items-center gap-3 mb-4">
        <p className="text-sm text-gray-400">{orphans.length.toLocaleString()} objects with no dependents.</p>
        <select value={filterCat} onChange={(e) => setFilterCat(e.target.value)}
          className="ml-auto bg-[#0f0f1a] border border-[#1e2d50] text-gray-300 text-xs rounded px-2 py-1">
          <option value="all">All categories</option>
          {categories.map((c) => <option key={c} value={c}>{c} ({orphans.filter((o) => o.category === c).length})</option>)}
        </select>
      </div>
      <div className="space-y-4">
        {Object.entries(grouped).sort((a, b) => b[1].length - a[1].length).map(([cat, items]) => (
          <div key={cat}>
            <div className="flex items-center gap-2 mb-2">
              <CategoryBadge category={cat} />
              <span className="text-xs text-gray-500">{items.length}</span>
            </div>
            <div className="bg-[#16213e] border border-[#1e2d50] rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#1e2d50] text-gray-500 text-xs">
                    <th className="text-left px-3 py-2 font-medium">Name</th>
                    <th className="text-left px-3 py-2 font-medium">Owner</th>
                    <th className="text-left px-3 py-2 font-medium">Modified</th>
                  </tr>
                </thead>
                <tbody>
                  {items.slice(0, 50).map((o) => (
                    <tr key={o.id} className="border-b border-[#1e2d50]/50 hover:bg-[#1a2a4a] cursor-pointer" onClick={() => onSelect(o.id)}>
                      <td className="px-3 py-1.5 text-blue-400 hover:text-blue-300">{o.name}</td>
                      <td className="px-3 py-1.5 text-gray-400">{o.owner || '-'}</td>
                      <td className="px-3 py-1.5 text-gray-500 text-xs">{o.dateModified?.split('T')[0] || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {items.length > 50 && <div className="px-3 py-2 text-xs text-gray-500">...and {items.length - 50} more</div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function StaleTab({ stale, onSelect }: { stale: StaleItem[]; onSelect: (id: string) => void }) {
  const [filterCat, setFilterCat] = useState<string>('all');
  const categories = [...new Set(stale.map((s) => s.category))].sort();
  const filtered = filterCat === 'all' ? stale : stale.filter((s) => s.category === filterCat);

  return (
    <div className="p-4">
      <div className="flex items-center gap-3 mb-4">
        <p className="text-sm text-gray-400">{stale.length.toLocaleString()} objects not modified in 365+ days.</p>
        <select value={filterCat} onChange={(e) => setFilterCat(e.target.value)}
          className="ml-auto bg-[#0f0f1a] border border-[#1e2d50] text-gray-300 text-xs rounded px-2 py-1">
          <option value="all">All categories</option>
          {categories.map((c) => <option key={c} value={c}>{c} ({stale.filter((s) => s.category === c).length})</option>)}
        </select>
      </div>
      <div className="bg-[#16213e] border border-[#1e2d50] rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[#1e2d50] text-gray-500 text-xs">
              <th className="text-left px-3 py-2 font-medium">Name</th>
              <th className="text-left px-3 py-2 font-medium">Category</th>
              <th className="text-left px-3 py-2 font-medium">Owner</th>
              <th className="text-left px-3 py-2 font-medium">Last Modified</th>
              <th className="text-right px-3 py-2 font-medium">Days Stale</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 100).map((s) => (
              <tr key={s.id} className="border-b border-[#1e2d50]/50 hover:bg-[#1a2a4a] cursor-pointer" onClick={() => onSelect(s.id)}>
                <td className="px-3 py-1.5 text-blue-400 hover:text-blue-300">{s.name}</td>
                <td className="px-3 py-1.5"><CategoryBadge category={s.category} /></td>
                <td className="px-3 py-1.5 text-gray-400">{s.owner || '-'}</td>
                <td className="px-3 py-1.5 text-gray-500 text-xs">{s.dateModified?.split('T')[0]}</td>
                <td className="px-3 py-1.5 text-right">
                  <span className={`text-xs font-medium ${s.daysSinceModified > 1000 ? 'text-red-400' : s.daysSinceModified > 500 ? 'text-yellow-400' : 'text-gray-400'}`}>
                    {s.daysSinceModified.toLocaleString()}d
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length > 100 && <div className="px-3 py-2 text-xs text-gray-500">Showing 100 of {filtered.length.toLocaleString()}</div>}
      </div>
    </div>
  );
}

function UnusedMetricsTab({ metrics, onSelect }: { metrics: UnusedMetric[]; onSelect: (id: string) => void }) {
  if (metrics.length === 0) return <div className="p-8 text-center text-gray-500">No unused metrics found.</div>;

  return (
    <div className="p-4">
      <p className="text-sm text-gray-400 mb-4">{metrics.length.toLocaleString()} metrics not referenced by any report or cube.</p>
      <div className="bg-[#16213e] border border-[#1e2d50] rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[#1e2d50] text-gray-500 text-xs">
              <th className="text-left px-3 py-2 font-medium">Metric Name</th>
              <th className="text-left px-3 py-2 font-medium">Owner</th>
              <th className="text-left px-3 py-2 font-medium">Last Modified</th>
            </tr>
          </thead>
          <tbody>
            {metrics.slice(0, 100).map((m) => (
              <tr key={m.id} className="border-b border-[#1e2d50]/50 hover:bg-[#1a2a4a] cursor-pointer" onClick={() => onSelect(m.id)}>
                <td className="px-3 py-1.5 text-blue-400 hover:text-blue-300">{m.name}</td>
                <td className="px-3 py-1.5 text-gray-400">{m.owner || '-'}</td>
                <td className="px-3 py-1.5 text-gray-500 text-xs">{m.dateModified?.split('T')[0] || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {metrics.length > 100 && <div className="px-3 py-2 text-xs text-gray-500">Showing 100 of {metrics.length.toLocaleString()}</div>}
      </div>
    </div>
  );
}

function ErrorsTab({ errors, onSelect }: { errors: ErrorItem[]; onSelect: (id: string) => void }) {
  const [filterType, setFilterType] = useState<string>('all');
  const [filterCat, setFilterCat] = useState<string>('all');

  const categories = [...new Set(errors.map(e => e.category))].sort();

  const filtered = useMemo(() => {
    let list = errors;
    if (filterType !== 'all') list = list.filter(e => e.errorType === filterType);
    if (filterCat !== 'all') list = list.filter(e => e.category === filterCat);
    return list;
  }, [errors, filterType, filterCat]);

  // Group by error type for summary
  const byType: Record<string, number> = {};
  for (const e of errors) byType[e.errorType] = (byType[e.errorType] || 0) + 1;

  if (errors.length === 0) return <div className="p-8 text-center text-gray-500">No errors found.</div>;

  return (
    <div className="p-4">
      {/* Summary cards */}
      <div className="flex gap-3 mb-4 flex-wrap">
        {Object.entries(byType).sort((a, b) => b[1] - a[1]).map(([type, count]) => (
          <button key={type} onClick={() => setFilterType(filterType === type ? 'all' : type)}
            className={`px-3 py-1.5 rounded text-xs border transition-colors ${
              filterType === type
                ? 'bg-red-500/20 border-red-500 text-red-300'
                : 'bg-[#16213e] border-[#1e2d50] text-gray-400 hover:text-white'
            }`}>
            {type}: {count}
          </button>
        ))}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 mb-4">
        <p className="text-sm text-gray-400">{filtered.length.toLocaleString()} errors</p>
        <select value={filterCat} onChange={(e) => setFilterCat(e.target.value)}
          className="ml-auto bg-[#0f0f1a] border border-[#1e2d50] text-gray-300 text-xs rounded px-2 py-1">
          <option value="all">All categories</option>
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>

      {/* Error table */}
      <div className="bg-[#16213e] border border-[#1e2d50] rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[#1e2d50] text-gray-500 text-xs">
              <th className="text-left px-3 py-2 font-medium">Object Name</th>
              <th className="text-left px-3 py-2 font-medium">Category</th>
              <th className="text-left px-3 py-2 font-medium">Error Type</th>
              <th className="text-left px-3 py-2 font-medium">Message</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 200).map((e, i) => (
              <tr key={`${e.id}-${e.errorType}-${i}`} className="border-b border-[#1e2d50]/50 hover:bg-[#1a2a4a] cursor-pointer" onClick={() => onSelect(e.id)}>
                <td className="px-3 py-1.5 text-blue-400 hover:text-blue-300">{e.name}</td>
                <td className="px-3 py-1.5"><CategoryBadge category={e.category} /></td>
                <td className="px-3 py-1.5">
                  <span className="text-xs px-1.5 py-0.5 rounded bg-red-500/20 text-red-300">{e.errorType}</span>
                </td>
                <td className="px-3 py-1.5 text-gray-500 text-xs max-w-md truncate" title={e.message}>{e.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length > 200 && <div className="px-3 py-2 text-xs text-gray-500">Showing 200 of {filtered.length.toLocaleString()}</div>}
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

interface RunInfo {
  id: string;
  label: string;
  timestamp: string;
  totalObjects: number;
  description?: string;
}

export default function Rationalization() {
  const { currentProject } = useApp();
  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>('');
  const [report, setReport] = useState<RationalizationReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<Tab>('overview');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const projectId = currentProject?.id;

  // Load runs index
  useEffect(() => {
    if (!projectId) { setLoading(false); setError('No project selected'); return; }
    setLoading(true);
    setError('');
    setRuns([]);
    setSelectedRunId('');
    setReport(null);
    fetch(`/projects/${projectId}/runs/runs.json`)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((data: RunInfo[]) => {
        setRuns(data);
        if (data.length > 0) {
          // Select the latest run by default
          setSelectedRunId(data[data.length - 1].id);
        } else {
          setLoading(false);
          setError('No runs found');
        }
      })
      .catch(() => {
        // Fallback: try old single-file path
        fetch(`/projects/${projectId}/rationalization_report.json`)
          .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
          .then((data) => { setReport(data); setLoading(false); })
          .catch((err) => { setError(err.message); setLoading(false); });
      });
  }, [projectId]);

  // Load selected run data
  useEffect(() => {
    if (!projectId || !selectedRunId) return;
    setLoading(true);
    setError('');
    setReport(null);
    setSelectedId(null);
    fetch(`/projects/${projectId}/runs/${selectedRunId}/rationalization_report.json`)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((data) => { setReport(data); setLoading(false); })
      .catch((err) => { setError(err.message); setLoading(false); });
  }, [projectId, selectedRunId]);

  const handleSelect = useCallback((id: string) => setSelectedId(id), []);
  const handleClose = useCallback(() => setSelectedId(null), []);

  if (loading) {
    return <div className="flex-1 flex items-center justify-center text-gray-500">Loading rationalization report...</div>;
  }

  if (error || !report) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-gray-500 p-6">
        <VscWarning className="text-4xl text-red-500 mb-3" />
        <p className="text-lg mb-1">No rationalization data found</p>
        <p className="text-sm text-gray-600">
          Run: <code className="bg-[#16213e] px-2 py-0.5 rounded text-xs text-gray-300">python mstr_project_rationalize.py</code>
        </p>
        {error && <p className="text-xs text-red-400 mt-2">{error}</p>}
      </div>
    );
  }

  const { summary } = report;

  const tabs: { key: Tab; label: string; count?: number }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'dependencies', label: 'Dependencies', count: summary.totalEdges },
    { key: 'duplicates', label: 'Duplicate SQL', count: summary.duplicateSqlGroupCount },
    { key: 'orphans', label: 'Orphans', count: summary.orphanCount },
    { key: 'stale', label: 'Stale Objects', count: summary.staleObjectCount },
    { key: 'unused-metrics', label: 'Unused Metrics', count: summary.unusedMetricCount },
    ...((report.errors?.length || 0) > 0 ? [{ key: 'errors' as Tab, label: 'Errors', count: summary.errorCount }] : []),
  ];

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="p-4 border-b border-[#1e2d50] bg-[#16213e]">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold text-white mb-1">Project Rationalization</h2>
            <p className="text-xs text-gray-400">
              {summary.totalObjects.toLocaleString()} objects, {summary.totalEdges.toLocaleString()} dependency edges
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

      {/* Tabs */}
      <div className="flex border-b border-[#1e2d50] bg-[#16213e]/50 overflow-x-auto">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2.5 text-sm transition-colors border-b-2 whitespace-nowrap ${
              tab === t.key ? 'text-white border-red-500' : 'text-gray-400 border-transparent hover:text-white hover:border-gray-600'
            }`}
          >
            {t.label}
            {t.count !== undefined && (
              <span className={`ml-2 text-xs px-1.5 py-0.5 rounded ${
                tab === t.key ? 'bg-red-500/20 text-red-300' : 'bg-[#0f0f1a] text-gray-500'
              }`}>{t.count.toLocaleString()}</span>
            )}
          </button>
        ))}
      </div>

      {/* Content + optional detail panel */}
      <div className="flex-1 flex overflow-hidden">
        <div className="flex-1 overflow-auto">
          {tab === 'overview' && (
            <div className="p-4 space-y-4">
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <StatCard label="Total Objects" value={summary.totalObjects} icon={VscSymbolMethod} color="bg-blue-500/20 text-blue-400" />
                <StatCard label="Duplicate SQL Groups" value={summary.duplicateSqlGroupCount} icon={VscCopy} color="bg-red-500/20 text-red-400" />
                <StatCard label="Orphan Objects" value={summary.orphanCount} icon={VscWarning} color="bg-yellow-500/20 text-yellow-400" />
                <StatCard label="Stale Objects" value={summary.staleObjectCount} icon={VscHistory} color="bg-orange-500/20 text-orange-400" />
              </div>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <StatCard label="Unused Metrics" value={summary.unusedMetricCount} icon={VscGraphLine} color="bg-purple-500/20 text-purple-400" />
                <StatCard label="Dependency Edges" value={summary.totalEdges} icon={VscGraphLine} color="bg-cyan-500/20 text-cyan-400" />
                <StatCard label="High Impact Objects" value={summary.highImpactCount} icon={VscWarning} color="bg-red-500/20 text-red-400" />
                <StatCard label="Enrichment Errors" value={summary.errorCount} icon={VscWarning} color="bg-gray-500/20 text-gray-400" />
              </div>
              <InventoryBreakdown byCategory={summary.byCategory} />
              <div className="bg-[#16213e] border border-[#1e2d50] rounded-lg p-4">
                <h3 className="text-sm font-semibold text-white mb-3">Key Findings</h3>
                <ul className="space-y-2 text-sm">
                  {summary.duplicateSqlGroupCount > 0 && (
                    <li className="flex items-start gap-2">
                      <VscCopy className="text-red-400 mt-0.5 shrink-0" />
                      <span className="text-gray-300"><strong className="text-red-400">{summary.duplicateSqlObjectCount}</strong> reports/cubes share identical SQL across <strong>{summary.duplicateSqlGroupCount}</strong> groups.</span>
                    </li>
                  )}
                  {summary.orphanCount > 0 && (
                    <li className="flex items-start gap-2">
                      <VscWarning className="text-yellow-400 mt-0.5 shrink-0" />
                      <span className="text-gray-300"><strong className="text-yellow-400">{summary.orphanCount.toLocaleString()}</strong> objects have no dependents — candidates for archival.</span>
                    </li>
                  )}
                  {summary.highImpactCount > 0 && (
                    <li className="flex items-start gap-2">
                      <VscGraphLine className="text-red-400 mt-0.5 shrink-0" />
                      <span className="text-gray-300"><strong className="text-red-400">{summary.highImpactCount}</strong> high-impact objects — changing these affects many downstream consumers.</span>
                    </li>
                  )}
                  {summary.staleObjectCount > 0 && (
                    <li className="flex items-start gap-2">
                      <VscHistory className="text-orange-400 mt-0.5 shrink-0" />
                      <span className="text-gray-300"><strong className="text-orange-400">{summary.staleObjectCount.toLocaleString()}</strong> objects haven't been modified in over a year.</span>
                    </li>
                  )}
                </ul>
              </div>
            </div>
          )}

          {tab === 'dependencies' && (
            <DependenciesTab
              objects={report.objects}
              dependentsOf={report.dependentsOf}
              dependenciesOf={report.dependenciesOf}
              highImpact={report.highImpact}
              onSelect={handleSelect}
            />
          )}
          {tab === 'duplicates' && <DuplicateSqlTab groups={report.duplicateSql} onSelect={handleSelect} />}
          {tab === 'orphans' && <OrphansTab orphans={report.orphans} onSelect={handleSelect} />}
          {tab === 'stale' && <StaleTab stale={report.staleObjects} onSelect={handleSelect} />}
          {tab === 'unused-metrics' && <UnusedMetricsTab metrics={report.unusedMetrics} onSelect={handleSelect} />}
          {tab === 'errors' && <ErrorsTab errors={report.errors || []} onSelect={handleSelect} />}
        </div>

        {/* Dependency detail panel */}
        {selectedId && (
          <DependencyDetailPanel
            objectId={selectedId}
            objects={report.objects}
            dependentsOf={report.dependentsOf}
            dependenciesOf={report.dependenciesOf}
            onNavigate={handleSelect}
            onClose={handleClose}
          />
        )}
      </div>
    </div>
  );
}
