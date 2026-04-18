import { useState, useRef, useCallback } from 'react';
import { VscPlay, VscStopCircle, VscDesktopDownload, VscChevronRight, VscChevronDown } from 'react-icons/vsc';
import {
  searchObjects,
  getReportModelDefinition,
  createReportInstanceForSql,
  getReportSqlView,
} from '../api/mstrClient';

// --- Types ---

interface InventoryRow {
  reportId: string;
  reportName: string;
  owner: string;
  dateModified: string;
  filterText: string;
  attributes: { id: string; name: string; subType?: string }[];
  metrics: { id: string; name: string; subType?: string }[];
  sql: string;
  sourceTables: string[];
  definitionError?: string;
  sqlError?: string;
}

// --- SQL table parser (mirrors Python script) ---

const SQL_TABLE_PATTERNS = [
  /\bfrom\s+([`"[\]\w.]+)/gi,
  /\bjoin\s+([`"[\]\w.]+)/gi,
];

function parseTableNamesFromSql(sql: string): string[] {
  if (!sql) return [];
  const results: string[] = [];
  for (const pattern of SQL_TABLE_PATTERNS) {
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(sql)) !== null) {
      let cleaned = match[1].trim().replace(/,$/,'').replace(/^[`"[\]]+|[`"[\]]+$/g, '');
      if (cleaned && !results.includes(cleaned)) results.push(cleaned);
    }
  }
  return results;
}

// --- Model definition extractors (mirrors Python script) ---

function extractUnits(reportDef: any): { attributes: any[]; metrics: any[] } {
  const units = reportDef?.dataSource?.dataTemplate?.units || [];
  const attributes: any[] = [];
  const metrics: any[] = [];

  for (const unit of units) {
    const unitType = unit.type || '';
    for (const el of unit.elements || []) {
      const entry = {
        id: el.id || el.objectId || '',
        name: el.name || '',
        subType: el.subType || unitType,
      };
      const sub = (entry.subType || '').toLowerCase();
      if (sub.includes('metric') || unitType === 'metrics') {
        metrics.push(entry);
      } else {
        attributes.push(entry);
      }
    }
  }
  return { attributes: dedupe(attributes), metrics: dedupe(metrics) };
}

function dedupe(items: any[]): any[] {
  const seen = new Set<string>();
  return items.filter(item => {
    const key = `${item.id}|${item.name}|${item.subType}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function extractFilterText(reportDef: any): string {
  const filter = reportDef?.dataSource?.filter;
  return filter?.text || '';
}

// --- Component ---

export default function ReportInventory() {
  const [inventory, setInventory] = useState<InventoryRow[]>([]);
  const [running, setRunning] = useState(false);
  const [includeSql, setIncludeSql] = useState(false);
  const [limit, setLimit] = useState(25);
  const [progress, setProgress] = useState({ current: 0, total: 0 });
  const [error, setError] = useState('');
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const cancelRef = useRef(false);

  const runInventory = useCallback(async () => {
    setRunning(true);
    setError('');
    setInventory([]);
    cancelRef.current = false;

    try {
      // Search all reports
      const data = await searchObjects({ type: 3, limit: Math.max(limit, 1) });
      const reports: any[] = data.result || [];
      setProgress({ current: 0, total: reports.length });

      const rows: InventoryRow[] = [];

      for (let i = 0; i < reports.length; i++) {
        if (cancelRef.current) break;

        const r = reports[i];
        const reportId = r.id || r.objectId;
        const row: InventoryRow = {
          reportId,
          reportName: r.name || '',
          owner: r.owner?.name || '',
          dateModified: r.dateModified || '',
          filterText: '',
          attributes: [],
          metrics: [],
          sql: '',
          sourceTables: [],
        };

        // Fetch model definition
        try {
          const modelDef = await getReportModelDefinition(reportId);
          const { attributes, metrics } = extractUnits(modelDef);
          row.attributes = attributes;
          row.metrics = metrics;
          row.filterText = extractFilterText(modelDef);
        } catch (err: any) {
          row.definitionError = err.response?.data?.message || err.message || 'Failed';
        }

        // Optionally fetch SQL
        if (includeSql && !cancelRef.current) {
          try {
            const instance = await createReportInstanceForSql(reportId);
            const instanceId = instance.instanceId || instance.id;
            if (instanceId) {
              const sqlData = await getReportSqlView(reportId, instanceId);
              row.sql = sqlData.sqlStatement || '';
              row.sourceTables = parseTableNamesFromSql(row.sql);
            }
          } catch (err: any) {
            row.sqlError = err.response?.data?.message || err.message || 'Failed';
          }
        }

        rows.push(row);
        setInventory([...rows]);
        setProgress({ current: i + 1, total: reports.length });
      }
    } catch (err: any) {
      setError(err.response?.data?.message || err.message || 'Failed to search reports');
    } finally {
      setRunning(false);
    }
  }, [includeSql, limit]);

  function stopInventory() {
    cancelRef.current = true;
  }

  function exportJson() {
    const blob = new Blob([JSON.stringify(inventory, null, 2)], { type: 'application/json' });
    downloadBlob(blob, 'report_inventory.json');
  }

  function exportCsv() {
    const headers = [
      'reportId', 'reportName', 'owner', 'dateModified',
      'filterText', 'attributeCount', 'attributes',
      'metricCount', 'metrics', 'sourceTables',
      'definitionError', 'sqlError',
    ];
    const csvRows = [headers.join(',')];
    for (const row of inventory) {
      csvRows.push([
        csvEscape(row.reportId),
        csvEscape(row.reportName),
        csvEscape(row.owner),
        csvEscape(row.dateModified),
        csvEscape(row.filterText),
        String(row.attributes.length),
        csvEscape(row.attributes.map(a => a.name).join('; ')),
        String(row.metrics.length),
        csvEscape(row.metrics.map(m => m.name).join('; ')),
        csvEscape(row.sourceTables.join('; ')),
        csvEscape(row.definitionError || ''),
        csvEscape(row.sqlError || ''),
      ].join(','));
    }
    const blob = new Blob([csvRows.join('\n')], { type: 'text/csv' });
    downloadBlob(blob, 'report_inventory.csv');
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Toolbar */}
      <div className="p-4 border-b border-[#1e2d50] bg-[#16213e]">
        <div className="flex items-center gap-4 mb-3">
          <h2 className="text-xl font-bold text-white">Report Inventory</h2>
          <div className="flex-1" />
          {inventory.length > 0 && !running && (
            <>
              <button onClick={exportJson} className="px-3 py-1.5 bg-[#0f0f1a] text-gray-400 hover:text-white rounded text-xs transition-colors flex items-center gap-1.5">
                <VscDesktopDownload /> JSON
              </button>
              <button onClick={exportCsv} className="px-3 py-1.5 bg-[#0f0f1a] text-gray-400 hover:text-white rounded text-xs transition-colors flex items-center gap-1.5">
                <VscDesktopDownload /> CSV
              </button>
            </>
          )}
        </div>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-gray-400">
            <span className="text-xs uppercase tracking-wider">Limit</span>
            <input
              type="number"
              value={limit}
              onChange={e => setLimit(Math.max(1, Number(e.target.value)))}
              disabled={running}
              className="w-20 px-2 py-1.5 bg-[#0f0f1a] border border-[#2a2a4a] rounded text-white text-sm focus:outline-none focus:border-red-500"
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={includeSql}
              onChange={e => setIncludeSql(e.target.checked)}
              disabled={running}
              className="accent-red-500"
            />
            <span>Include SQL &amp; Source Tables</span>
          </label>
          <div className="flex-1" />
          {running ? (
            <button onClick={stopInventory} className="px-4 py-2 bg-yellow-600 hover:bg-yellow-700 text-white text-sm font-medium rounded-lg transition-colors flex items-center gap-2">
              <VscStopCircle /> Stop ({progress.current}/{progress.total})
            </button>
          ) : (
            <button onClick={runInventory} className="px-4 py-2 bg-gradient-to-r from-red-600 to-red-700 hover:from-red-700 hover:to-red-800 text-white text-sm font-medium rounded-lg transition-colors flex items-center gap-2">
              <VscPlay /> Run Inventory
            </button>
          )}
        </div>
        {running && progress.total > 0 && (
          <div className="mt-3">
            <div className="w-full h-1.5 bg-[#0f0f1a] rounded-full overflow-hidden">
              <div
                className="h-full bg-red-500 transition-all duration-300"
                style={{ width: `${(progress.current / progress.total) * 100}%` }}
              />
            </div>
            <p className="text-xs text-gray-500 mt-1">
              Processing {progress.current} of {progress.total} reports...
            </p>
          </div>
        )}
      </div>

      {error && (
        <div className="m-4 p-4 bg-red-900/30 border border-red-800 rounded-lg text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Results table */}
      {inventory.length > 0 && (
        <div className="flex-1 overflow-auto">
          <table className="w-full text-sm border-collapse">
            <thead className="sticky top-0 z-10">
              <tr className="bg-[#1a2a4a] text-gray-400 text-xs uppercase tracking-wider">
                <th className="px-3 py-2.5 font-medium w-8" />
                <th className="px-3 py-2.5 font-medium text-left">Report</th>
                <th className="px-3 py-2.5 font-medium text-left">Owner</th>
                <th className="px-3 py-2.5 font-medium text-center">Attrs</th>
                <th className="px-3 py-2.5 font-medium text-center">Metrics</th>
                <th className="px-3 py-2.5 font-medium text-left">Filter</th>
                {includeSql && <th className="px-3 py-2.5 font-medium text-center">Tables</th>}
                <th className="px-3 py-2.5 font-medium text-center">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1a1a2e]">
              {inventory.map(row => {
                const expanded = expandedRow === row.reportId;
                const hasError = row.definitionError || row.sqlError;
                return (
                  <InventoryRowView
                    key={row.reportId}
                    row={row}
                    expanded={expanded}
                    includeSql={includeSql}
                    hasError={!!hasError}
                    onToggle={() => setExpandedRow(expanded ? null : row.reportId)}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Empty state */}
      {!running && inventory.length === 0 && !error && (
        <div className="flex-1 flex flex-col items-center justify-center text-gray-500 p-6">
          <p className="mb-2">No inventory data yet</p>
          <p className="text-xs text-gray-600">
            Configure options above and click "Run Inventory" to extract report metadata,
            filters, attributes, metrics, and optionally SQL source tables.
          </p>
        </div>
      )}
    </div>
  );
}

// --- Row sub-component ---

function InventoryRowView({ row, expanded, includeSql, hasError, onToggle }: {
  row: InventoryRow;
  expanded: boolean;
  includeSql: boolean;
  hasError: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        className="bg-[#16213e] hover:bg-[#1a2a4a] transition-colors cursor-pointer"
      >
        <td className="px-3 py-2.5 text-gray-500">
          {expanded ? <VscChevronDown /> : <VscChevronRight />}
        </td>
        <td className="px-3 py-2.5">
          <div className="text-white font-medium">{row.reportName}</div>
          <div className="text-[10px] text-gray-600 font-mono">{row.reportId}</div>
        </td>
        <td className="px-3 py-2.5 text-gray-400">{row.owner || '-'}</td>
        <td className="px-3 py-2.5 text-center">
          <span className="text-blue-400 font-mono text-xs">{row.attributes.length}</span>
        </td>
        <td className="px-3 py-2.5 text-center">
          <span className="text-green-400 font-mono text-xs">{row.metrics.length}</span>
        </td>
        <td className="px-3 py-2.5 text-gray-400 max-w-[200px] truncate" title={row.filterText}>
          {row.filterText || <span className="text-gray-600">-</span>}
        </td>
        {includeSql && (
          <td className="px-3 py-2.5 text-center">
            <span className="text-purple-400 font-mono text-xs">{row.sourceTables.length}</span>
          </td>
        )}
        <td className="px-3 py-2.5 text-center">
          {hasError ? (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-900/30 text-yellow-400">partial</span>
          ) : (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-900/30 text-green-400">ok</span>
          )}
        </td>
      </tr>
      {expanded && (
        <tr className="bg-[#0a0a15]">
          <td colSpan={includeSql ? 8 : 7} className="px-4 py-4">
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {/* Attributes */}
              <div className="bg-[#16213e] rounded-lg p-3 border border-[#1e2d50]">
                <h4 className="text-xs text-blue-400 uppercase tracking-wider font-medium mb-2">
                  Attributes ({row.attributes.length})
                </h4>
                {row.attributes.length === 0 ? (
                  <p className="text-xs text-gray-600">None</p>
                ) : (
                  <div className="space-y-1">
                    {row.attributes.map(a => (
                      <div key={a.id} className="text-xs">
                        <span className="text-gray-200">{a.name}</span>
                        {a.subType && <span className="text-gray-600 ml-1.5">({a.subType})</span>}
                        <div className="text-[10px] text-gray-600 font-mono">{a.id}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Metrics */}
              <div className="bg-[#16213e] rounded-lg p-3 border border-[#1e2d50]">
                <h4 className="text-xs text-green-400 uppercase tracking-wider font-medium mb-2">
                  Metrics ({row.metrics.length})
                </h4>
                {row.metrics.length === 0 ? (
                  <p className="text-xs text-gray-600">None</p>
                ) : (
                  <div className="space-y-1">
                    {row.metrics.map(m => (
                      <div key={m.id} className="text-xs">
                        <span className="text-gray-200">{m.name}</span>
                        {m.subType && <span className="text-gray-600 ml-1.5">({m.subType})</span>}
                        <div className="text-[10px] text-gray-600 font-mono">{m.id}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Filter */}
              <div className="bg-[#16213e] rounded-lg p-3 border border-[#1e2d50]">
                <h4 className="text-xs text-yellow-400 uppercase tracking-wider font-medium mb-2">Filter</h4>
                {row.filterText ? (
                  <p className="text-xs text-gray-300 whitespace-pre-wrap">{row.filterText}</p>
                ) : (
                  <p className="text-xs text-gray-600">No filter</p>
                )}
              </div>

              {/* Source Tables */}
              {includeSql && (
                <div className="bg-[#16213e] rounded-lg p-3 border border-[#1e2d50]">
                  <h4 className="text-xs text-purple-400 uppercase tracking-wider font-medium mb-2">
                    Source Tables ({row.sourceTables.length})
                  </h4>
                  {row.sourceTables.length === 0 ? (
                    <p className="text-xs text-gray-600">{row.sqlError ? 'Error fetching SQL' : 'None'}</p>
                  ) : (
                    <div className="space-y-1">
                      {row.sourceTables.map(t => (
                        <div key={t} className="text-xs text-gray-200 font-mono">{t}</div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* SQL */}
              {includeSql && row.sql && (
                <div className="bg-[#16213e] rounded-lg p-3 border border-[#1e2d50] lg:col-span-2">
                  <h4 className="text-xs text-purple-400 uppercase tracking-wider font-medium mb-2">SQL</h4>
                  <pre className="text-xs text-gray-300 whitespace-pre-wrap overflow-auto max-h-40 font-mono">
                    {row.sql}
                  </pre>
                </div>
              )}

              {/* Errors */}
              {(row.definitionError || row.sqlError) && (
                <div className="bg-[#16213e] rounded-lg p-3 border border-red-900/50">
                  <h4 className="text-xs text-red-400 uppercase tracking-wider font-medium mb-2">Errors</h4>
                  {row.definitionError && <p className="text-xs text-red-300 mb-1">Definition: {row.definitionError}</p>}
                  {row.sqlError && <p className="text-xs text-red-300">SQL: {row.sqlError}</p>}
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// --- Helpers ---

function csvEscape(value: string): string {
  if (value.includes(',') || value.includes('"') || value.includes('\n')) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
