import { useEffect, useState } from 'react';
import { VscGlobe, VscChevronRight, VscSearch } from 'react-icons/vsc';

// ---- Types ----
interface CrossSummary {
  nameMatches: number;
  sqlMatches: number;
  sharedTables: number;
  sharedMetrics: number;
  sharedFamilies: number;
  projects: string[];
}

interface NameMatchMember {
  projectId: string; reportId: string; name: string; path: string; executions: number;
}

interface NameMatch {
  id: string; normalizedName: string; nProjects: number; nReports: number;
  projects: string[]; members: NameMatchMember[];
}

interface SharedTable {
  tableName: string; nProjects: number; nReferences: number; projects: string[];
}
interface SharedMetric {
  metricName: string; nProjects: number; nReferences: number; projects: string[];
}
interface SharedFamily {
  familyBase: string; nProjects: number; nReports: number; projects: string[];
}

type TabKey = 'overview' | 'name' | 'sql' | 'families' | 'tables' | 'metrics';

const TAB_LABELS: Record<TabKey, string> = {
  overview: 'Overview',
  name: 'Name Matches',
  sql: 'SQL Matches',
  families: 'Shared Families',
  tables: 'Shared Tables',
  metrics: 'Shared Metrics',
};

const BASE = '/data/_cross';

export default function CrossProject() {
  const [tab, setTab] = useState<TabKey>('overview');
  const [summary, setSummary] = useState<CrossSummary | null>(null);
  const [names, setNames] = useState<NameMatch[]>([]);
  const [families, setFamilies] = useState<SharedFamily[]>([]);
  const [tables, setTables] = useState<SharedTable[]>([]);
  const [metrics, setMetrics] = useState<SharedMetric[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedName, setSelectedName] = useState<NameMatch | null>(null);
  const [search, setSearch] = useState('');

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetch(`${BASE}/summary.json`).then((r) => r.json()),
      fetch(`${BASE}/name_matches.json`).then((r) => r.json()),
      fetch(`${BASE}/shared_families.json`).then((r) => r.json()),
      fetch(`${BASE}/shared_tables.json`).then((r) => r.json()),
      fetch(`${BASE}/shared_metrics.json`).then((r) => r.json()),
    ])
      .then(([s, n, f, t, m]) => {
        setSummary(s);
        setNames(n);
        setFamilies(f);
        setTables(t);
        setMetrics(m);
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message || 'Failed to load cross-project data');
        setLoading(false);
      });
  }, []);

  if (loading) return <div className="flex-1 flex items-center justify-center text-gray-400">Loading cross-project data...</div>;
  if (error) return (
    <div className="flex-1 flex items-center justify-center text-center">
      <div>
        <div className="text-red-400 text-xl mb-2">Failed to load</div>
        <div className="text-sm text-gray-500">{error}</div>
        <div className="text-xs mt-4 text-gray-600">
          Run <code className="bg-[#16213e] px-2 py-1 rounded">python -m db.compute.build --project all</code> to generate cross-project data.
        </div>
      </div>
    </div>
  );
  if (!summary) return null;

  return (
    <div className="flex-1 bg-[#0f0f1a] overflow-auto">
      <div className="p-6 max-w-7xl mx-auto">
        <div className="flex items-center gap-3 mb-6">
          <VscGlobe className="text-3xl text-green-400" />
          <div>
            <h1 className="text-2xl font-bold text-white">Cross-Project Rationalization</h1>
            <p className="text-sm text-gray-400">
              Reports and patterns that appear across multiple MSTR projects — consolidation candidates.
            </p>
          </div>
        </div>

        {/* Tab bar */}
        <div className="bg-[#16213e] rounded-lg mb-4 flex overflow-x-auto">
          {(Object.keys(TAB_LABELS) as TabKey[]).map((k) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              className={`px-5 py-3 text-sm font-medium whitespace-nowrap transition-colors border-b-2 ${
                tab === k ? 'text-white border-green-400' : 'text-gray-400 border-transparent hover:text-gray-200'
              }`}
            >
              {TAB_LABELS[k]}
            </button>
          ))}
        </div>

        {tab === 'overview' && (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
            <MetricCard label="Shared Names" value={summary.nameMatches} color="text-blue-400" />
            <MetricCard label="Identical SQL" value={summary.sqlMatches} color="text-purple-400" />
            <MetricCard label="Shared Families" value={summary.sharedFamilies} color="text-amber-400" />
            <MetricCard label="Shared Tables" value={summary.sharedTables} color="text-green-400" />
            <MetricCard label="Shared Metrics" value={summary.sharedMetrics} color="text-pink-400" />
          </div>
        )}

        {tab === 'overview' && (
          <div className="bg-[#16213e] p-5 rounded-lg">
            <h2 className="text-lg font-bold text-white mb-3">What this covers</h2>
            <div className="space-y-2 text-sm text-gray-300">
              <p><b className="text-blue-400">Name Matches</b> — reports with the same normalized name in 2+ projects. Likely migrations / cross-project copies.</p>
              <p><b className="text-purple-400">Identical SQL</b> — byte-identical normalized SQL across projects. The strongest duplicate signal.</p>
              <p><b className="text-amber-400">Shared Families</b> — name-pattern roots (before " - ") shared across projects. "GFE085a - Projections by PD" style.</p>
              <p><b className="text-green-400">Shared Tables</b> — source tables queried by multiple projects. Indicates common data-access patterns.</p>
              <p><b className="text-pink-400">Shared Metrics</b> — metric definitions reused in multiple projects. Core KPIs that could be standardized.</p>
            </div>
            <p className="text-xs text-gray-500 mt-4">
              Computed from <code className="bg-[#0f0f1a] px-1 rounded">cross_project_*</code> tables in the SQLite DB,
              emitted to <code className="bg-[#0f0f1a] px-1 rounded">public/data/_cross/*.json</code>.
            </p>
          </div>
        )}

        {tab === 'name' && (
          <NameMatchList names={names} search={search} setSearch={setSearch} onOpen={setSelectedName} />
        )}
        {tab === 'sql' && (
          <div className="bg-[#16213e] p-6 rounded-lg text-gray-400">
            <h2 className="text-lg font-bold text-white mb-2">Identical SQL Across Projects</h2>
            <p className="text-sm">
              No byte-identical SQL detected across projects. Each project runs distinct queries (no direct copy-paste
              migrations). If you want structural matches, check the per-project AST tabs.
            </p>
          </div>
        )}
        {tab === 'families' && <SharedFamilyList families={families} search={search} setSearch={setSearch} />}
        {tab === 'tables' && <SharedTableList tables={tables} search={search} setSearch={setSearch} />}
        {tab === 'metrics' && <SharedMetricList metrics={metrics} search={search} setSearch={setSearch} />}

        {selectedName && <NameMatchModal match={selectedName} onClose={() => setSelectedName(null)} />}
      </div>
    </div>
  );
}

function MetricCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="bg-[#16213e] p-4 rounded-lg border-l-4 border-green-500">
      <div className="text-xs text-gray-400 uppercase tracking-wider">{label}</div>
      <div className={`text-3xl font-bold mt-1 ${color}`}>{value.toLocaleString()}</div>
    </div>
  );
}

function SearchBar({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <div className="relative mb-4">
      <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-[#0f0f1a] border border-[#1e2d50] rounded-lg pl-10 pr-4 py-3 text-white text-sm focus:outline-none focus:border-green-500"
      />
    </div>
  );
}

function NameMatchList({ names, search, setSearch, onOpen }: {
  names: NameMatch[]; search: string; setSearch: (v: string) => void; onOpen: (n: NameMatch) => void;
}) {
  const q = search.toLowerCase().trim();
  const filtered = q ? names.filter((n) => n.normalizedName.includes(q)) : names;
  return (
    <div className="bg-[#16213e] p-5 rounded-lg">
      <h2 className="text-lg font-bold text-white mb-3">
        Report Names Shared Across Projects
        <span className="text-sm text-gray-400 font-normal ml-2">({filtered.length} shown)</span>
      </h2>
      <SearchBar value={search} onChange={setSearch} placeholder="Search by report name..." />
      <div className="space-y-2 max-h-[70vh] overflow-y-auto pr-2">
        {filtered.map((n) => (
          <div
            key={n.id}
            onClick={() => onOpen(n)}
            className="bg-[#0f0f1a] p-3 rounded-lg cursor-pointer hover:bg-[#1a2a4a] transition-colors border-l-4 border-blue-500"
          >
            <div className="flex justify-between items-center">
              <div className="flex-1 min-w-0">
                <div className="text-white text-sm font-semibold">{n.normalizedName}</div>
                <div className="text-xs text-gray-500 mt-1">
                  {n.projects.join(' · ')} · {n.nReports} reports
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <div className="bg-blue-600 text-white text-xs font-bold px-2 py-1 rounded-full">
                  {n.nProjects} projects
                </div>
                <VscChevronRight className="text-gray-500" />
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function NameMatchModal({ match, onClose }: { match: NameMatch; onClose: () => void }) {
  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-[#16213e] rounded-lg w-full max-w-4xl max-h-[85vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="p-5 border-b border-[#1e2d50] flex justify-between items-start">
          <div>
            <h3 className="text-lg font-bold text-white">{match.normalizedName}</h3>
            <div className="text-xs text-gray-400 mt-1">
              {match.nReports} reports across {match.nProjects} projects: {match.projects.join(', ')}
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-2xl leading-none">{'\u00d7'}</button>
        </div>
        <div className="flex-1 overflow-auto p-5">
          <table className="w-full text-xs">
            <thead className="bg-[#0f0f1a] sticky top-0">
              <tr className="text-gray-400 text-left">
                <th className="p-2 font-normal">Project</th>
                <th className="p-2 font-normal">Report</th>
                <th className="p-2 font-normal">Path</th>
                <th className="p-2 font-normal text-right">Executions</th>
              </tr>
            </thead>
            <tbody>
              {match.members.sort((a, b) => (b.executions || 0) - (a.executions || 0)).map((m) => (
                <tr key={`${m.projectId}/${m.reportId}`} className="border-b border-[#1e2d50]/50">
                  <td className="p-2 text-blue-300 whitespace-nowrap">{m.projectId}</td>
                  <td className="p-2 text-gray-200">{m.name}</td>
                  <td className="p-2 text-gray-500 text-[11px]">{m.path}</td>
                  <td className="p-2 text-right font-mono text-gray-300">{m.executions.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function SharedFamilyList({ families, search, setSearch }: { families: SharedFamily[]; search: string; setSearch: (v: string) => void }) {
  const q = search.toLowerCase().trim();
  const filtered = q ? families.filter((f) => f.familyBase.toLowerCase().includes(q)) : families;
  return (
    <div className="bg-[#16213e] p-5 rounded-lg">
      <h2 className="text-lg font-bold text-white mb-3">
        Report Families Shared Across Projects
        <span className="text-sm text-gray-400 font-normal ml-2">({filtered.length} shown)</span>
      </h2>
      <SearchBar value={search} onChange={setSearch} placeholder="Search by family base..." />
      <div className="space-y-2 max-h-[70vh] overflow-y-auto pr-2">
        {filtered.map((f) => (
          <div key={f.familyBase} className="bg-[#0f0f1a] p-3 rounded-lg border-l-4 border-amber-500">
            <div className="flex justify-between items-center">
              <div className="flex-1 min-w-0">
                <div className="text-white text-sm font-semibold">{f.familyBase}</div>
                <div className="text-xs text-gray-500 mt-1">{f.projects.join(' · ')}</div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <div className="bg-amber-600 text-white text-xs font-bold px-2 py-1 rounded-full">{f.nReports} variants</div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SharedTableList({ tables, search, setSearch }: { tables: SharedTable[]; search: string; setSearch: (v: string) => void }) {
  const q = search.toLowerCase().trim();
  const filtered = q ? tables.filter((t) => t.tableName.toLowerCase().includes(q)) : tables;
  return (
    <div className="bg-[#16213e] p-5 rounded-lg">
      <h2 className="text-lg font-bold text-white mb-3">
        Tables Used in 2+ Projects
        <span className="text-sm text-gray-400 font-normal ml-2">({filtered.length} shown)</span>
      </h2>
      <SearchBar value={search} onChange={setSearch} placeholder="Search by table name..." />
      <div className="max-h-[70vh] overflow-y-auto pr-2">
        <table className="w-full text-xs">
          <thead className="bg-[#0f0f1a] sticky top-0">
            <tr className="text-gray-400 text-left">
              <th className="p-2 font-normal">Table</th>
              <th className="p-2 font-normal">Projects</th>
              <th className="p-2 font-normal text-right">References</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 500).map((t) => (
              <tr key={t.tableName} className="border-b border-[#1e2d50]/50 hover:bg-[#0f0f1a]">
                <td className="p-2 text-gray-200 font-mono">{t.tableName}</td>
                <td className="p-2 text-blue-300 text-[11px]">{t.projects.join(', ')}</td>
                <td className="p-2 text-right font-mono text-gray-300">{t.nReferences.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SharedMetricList({ metrics, search, setSearch }: { metrics: SharedMetric[]; search: string; setSearch: (v: string) => void }) {
  const q = search.toLowerCase().trim();
  const filtered = q ? metrics.filter((m) => m.metricName.toLowerCase().includes(q)) : metrics;
  return (
    <div className="bg-[#16213e] p-5 rounded-lg">
      <h2 className="text-lg font-bold text-white mb-3">
        Metrics Used in 2+ Projects
        <span className="text-sm text-gray-400 font-normal ml-2">({filtered.length} shown)</span>
      </h2>
      <SearchBar value={search} onChange={setSearch} placeholder="Search by metric name..." />
      <div className="max-h-[70vh] overflow-y-auto pr-2">
        <table className="w-full text-xs">
          <thead className="bg-[#0f0f1a] sticky top-0">
            <tr className="text-gray-400 text-left">
              <th className="p-2 font-normal">Metric</th>
              <th className="p-2 font-normal">Projects</th>
              <th className="p-2 font-normal text-right">References</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 500).map((m) => (
              <tr key={m.metricName} className="border-b border-[#1e2d50]/50 hover:bg-[#0f0f1a]">
                <td className="p-2 text-gray-200">{m.metricName}</td>
                <td className="p-2 text-pink-300 text-[11px]">{m.projects.join(', ')}</td>
                <td className="p-2 text-right font-mono text-gray-300">{m.nReferences.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
