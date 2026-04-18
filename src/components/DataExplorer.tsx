import { useState, useEffect, useCallback } from 'react';
import { useApp } from '../context/AppContext';
import { VscPlay, VscArrowSmallLeft, VscArrowSmallRight, VscSymbolProperty } from 'react-icons/vsc';
import {
  searchObjects,
  getV2ReportDefinition, getV2CubeDefinition,
  getReportDefinition, getCubeDefinition,
  createV2ReportInstance, createV2CubeInstance,
  getV2ReportInstanceData, getV2CubeInstanceData,
  getAttributeElements,
} from '../api/mstrClient';
import type { V2InstanceBody } from '../api/mstrClient';
import { parseV2Definition, parseV2Grid } from '../api/v2GridParser';
import type { GridAttribute, GridMetric, ParsedColumn } from '../api/v2GridParser';
import RequestedObjects from './query/RequestedObjects';
import FilterBuilder, { buildViewFilter } from './query/FilterBuilder';
import type { FilterExpression } from './query/FilterBuilder';
import SortBuilder, { buildSorting } from './query/SortBuilder';
import type { SortCriteria } from './query/SortBuilder';
import MetricLimitsComponent, { buildMetricLimits } from './query/MetricLimits';
import type { MetricLimit } from './query/MetricLimits';

interface DatasetOption {
  id: string;
  name: string;
  type: number;
}

const PAGE_SIZE = 100;

export default function DataExplorer() {
  const { currentProject } = useApp();
  // --- Dataset picker ---
  const [datasets, setDatasets] = useState<DatasetOption[]>([]);
  const [datasetsLoading, setDatasetsLoading] = useState(false);
  const [selectedDataset, setSelectedDataset] = useState<DatasetOption | null>(null);

  // --- Definition ---
  const [defAttributes, setDefAttributes] = useState<GridAttribute[]>([]);
  const [defMetrics, setDefMetrics] = useState<GridMetric[]>([]);
  const [defLoading, setDefLoading] = useState(false);
  const [rawDefinition, setRawDefinition] = useState<any>(null);

  // --- Query options ---
  const [selectedAttrIds, setSelectedAttrIds] = useState<Set<string>>(new Set());
  const [selectedMetricIds, setSelectedMetricIds] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState<FilterExpression[]>([]);
  const [sorting, setSorting] = useState<SortCriteria[]>([]);
  const [metricLimits, setMetricLimits] = useState<MetricLimit[]>([]);

  // --- Filter elements ---
  const [filterElements, setFilterElements] = useState<{ id: string; name: string }[]>([]);
  const [filterElementsLoading, setFilterElementsLoading] = useState(false);

  // --- Instance / data ---
  const [instanceId, setInstanceId] = useState<string | null>(null);
  const [columns, setColumns] = useState<ParsedColumn[]>([]);
  const [rows, setRows] = useState<string[][]>([]);
  const [totalRows, setTotalRows] = useState(0);
  const [pageOffset, setPageOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // --- Metadata ---
  const [showMetadata, setShowMetadata] = useState(false);

  const isReport = selectedDataset?.type === 3;

  // --- Load datasets on mount ---
  useEffect(() => {
    loadDatasets();
  }, []);

  async function loadDatasets() {
    setDatasetsLoading(true);
    try {
      const [reportsRes, cubesRes] = await Promise.allSettled([
        searchObjects({ type: 768, limit: 25 }),
        searchObjects({ type: 776, limit: 25 }),
      ]);
      const reports = (reportsRes.status === 'fulfilled' ? reportsRes.value.result || [] : [])
        .map((r: any) => ({ id: r.id, name: r.name, type: 3 }));
      const cubes = (cubesRes.status === 'fulfilled' ? cubesRes.value.result || [] : [])
        .map((c: any) => ({ id: c.id, name: c.name, type: 21 }));
      setDatasets([...reports, ...cubes]);
    } catch {
      // non-critical
    } finally {
      setDatasetsLoading(false);
    }
  }

  // --- When dataset changes, load definition ---
  function handleDatasetChange(datasetId: string) {
    const ds = datasets.find(d => d.id === datasetId);
    if (!ds) return;
    setSelectedDataset(ds);
    setInstanceId(null);
    setColumns([]);
    setRows([]);
    setTotalRows(0);
    setPageOffset(0);
    setError('');
    setFilters([]);
    setSorting([]);
    setMetricLimits([]);
    setShowMetadata(false);
    setRawDefinition(null);
    loadDefinition(ds);
  }

  async function loadDefinition(ds: DatasetOption) {
    setDefLoading(true);
    setDefAttributes([]);
    setDefMetrics([]);
    setRawDefinition(null);
    try {
      const fn = ds.type === 3 ? getV2ReportDefinition : getV2CubeDefinition;
      const data = await fn(ds.id);
      setRawDefinition(data);
      applyDefinition(data);
    } catch {
      try {
        const fn = ds.type === 3 ? getReportDefinition : getCubeDefinition;
        const data = await fn(ds.id);
        setRawDefinition(data);
        applyDefinition(data);
      } catch {
        // empty
      }
    } finally {
      setDefLoading(false);
    }
  }

  function applyDefinition(data: any) {
    const { attributes, metrics } = parseV2Definition(data);
    setDefAttributes(attributes);
    setDefMetrics(metrics);
    setSelectedAttrIds(new Set(attributes.map(a => a.id)));
    setSelectedMetricIds(new Set(metrics.map(m => m.id)));
  }

  // --- Build POST body ---
  function buildBody(): V2InstanceBody {
    const body: V2InstanceBody = {};
    const allAttrs = selectedAttrIds.size === defAttributes.length;
    const allMetrics = selectedMetricIds.size === defMetrics.length;
    if (!allAttrs || !allMetrics) {
      body.requestedObjects = {
        attributes: defAttributes.filter(a => selectedAttrIds.has(a.id)).map(a => ({ id: a.id })),
        metrics: defMetrics.filter(m => selectedMetricIds.has(m.id)).map(m => ({ id: m.id })),
      };
    }
    const vf = buildViewFilter(filters);
    if (vf) body.viewFilter = vf;
    const ml = buildMetricLimits(metricLimits);
    if (ml) body.metricLimits = ml;
    const s = buildSorting(sorting);
    if (s) body.sorting = s;
    return body;
  }

  // --- Execute ---
  const execute = useCallback(async (offset = 0) => {
    if (!selectedDataset) return;
    setLoading(true);
    setError('');
    try {
      const createFn = isReport ? createV2ReportInstance : createV2CubeInstance;
      const data = await createFn(selectedDataset.id, buildBody(), offset, PAGE_SIZE);
      const instId = data.instanceId || data.id;
      setInstanceId(instId);
      const grid = parseV2Grid(data);
      setColumns(grid.columns);
      setRows(grid.rows);
      setTotalRows(grid.totalRows);
      setPageOffset(offset);
    } catch (err: any) {
      setError(err.response?.data?.message || err.message || 'Execution failed');
    } finally {
      setLoading(false);
    }
  }, [selectedDataset, isReport, selectedAttrIds, selectedMetricIds, filters, sorting, metricLimits, defAttributes, defMetrics]);

  // --- Pagination ---
  async function fetchPage(offset: number) {
    if (!selectedDataset || !instanceId) return;
    setLoading(true);
    setError('');
    try {
      const getFn = isReport ? getV2ReportInstanceData : getV2CubeInstanceData;
      const data = await getFn(selectedDataset.id, instanceId, offset, PAGE_SIZE);
      const grid = parseV2Grid(data);
      setColumns(grid.columns);
      setRows(grid.rows);
      setTotalRows(grid.totalRows);
      setPageOffset(offset);
    } catch (err: any) {
      if (err.response?.status === 404) {
        await execute(offset);
        return;
      }
      setError(err.response?.data?.message || err.message || 'Failed to fetch page');
    } finally {
      setLoading(false);
    }
  }

  const currentPage = Math.floor(pageOffset / PAGE_SIZE) + 1;
  const totalPages = Math.ceil(totalRows / PAGE_SIZE) || 1;

  // --- Attribute elements for filter list ---
  async function loadFilterElements(attributeId: string) {
    if (!instanceId || !selectedDataset) { setFilterElements([]); return; }
    setFilterElementsLoading(true);
    try {
      const objType = isReport ? 'reports' as const : 'cubes' as const;
      const data = await getAttributeElements(objType, selectedDataset.id, instanceId, attributeId);
      setFilterElements((Array.isArray(data) ? data : data?.elements || []).map((el: any) => ({
        id: el.id, name: el.formValues?.[0] || el.name || el.id,
      })));
    } catch {
      setFilterElements([]);
    } finally {
      setFilterElementsLoading(false);
    }
  }

  // --- Metadata ---
  function toggleMetadata() {
    setShowMetadata(prev => !prev);
  }

  // --- Column toggles ---
  function toggleAttr(id: string) {
    setSelectedAttrIds(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  }
  function toggleMetric(id: string) {
    setSelectedMetricIds(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header: dataset picker + execute */}
      <div className="px-6 py-4 border-b border-[#1e2d50] bg-[#16213e]">
        <div className="flex items-center gap-4">
          <h2 className="text-lg font-bold text-white shrink-0">Data Explorer</h2>
          <select
            value={selectedDataset?.id || ''}
            onChange={e => handleDatasetChange(e.target.value)}
            disabled={datasetsLoading}
            className="flex-1 max-w-lg px-3 py-2 bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg text-sm text-white focus:outline-none focus:border-red-500"
          >
            <option value="">{datasetsLoading ? 'Loading datasets...' : 'Select a dataset...'}</option>
            {datasets.some(d => d.type === 3) && (
              <optgroup label="Reports">
                {datasets.filter(d => d.type === 3).map(d => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </optgroup>
            )}
            {datasets.some(d => d.type === 21) && (
              <optgroup label="Cubes">
                {datasets.filter(d => d.type === 21).map(d => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </optgroup>
            )}
          </select>
          <button
            onClick={toggleMetadata}
            disabled={!rawDefinition}
            className={`px-4 py-2 rounded-lg text-sm transition-colors flex items-center gap-2 shrink-0 ${showMetadata ? 'bg-blue-600 text-white' : 'bg-[#0f0f1a] border border-[#2a2a4a] text-gray-400 hover:text-white'}`}
          >
            <VscSymbolProperty /> Metadata
          </button>
          <button
            onClick={() => execute(0)}
            disabled={!selectedDataset || loading || defLoading}
            className="px-5 py-2 bg-gradient-to-r from-red-600 to-red-700 hover:from-red-700 hover:to-red-800 text-white text-sm font-medium rounded-lg transition-all disabled:opacity-50 flex items-center gap-2 shrink-0"
          >
            <VscPlay /> Execute
          </button>
        </div>
        {selectedDataset && (
          <p className="text-xs text-gray-500 mt-1.5">
            {currentProject?.name} &middot; {isReport ? 'Report' : 'Cube'} &middot; {defAttributes.length} attributes &middot; {defMetrics.length} metrics
            {totalRows > 0 && <> &middot; {totalRows} rows</>}
          </p>
        )}
      </div>

      {/* Metadata panel — raw definition JSON */}
      {showMetadata && rawDefinition && (
        <div className="px-6 py-4 border-b border-[#1e2d50] bg-[#0a0a15] max-h-[45vh] overflow-auto">
          <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap break-words">
            {JSON.stringify(rawDefinition, null, 2)}
          </pre>
        </div>
      )}

      {/* Four query option boxes */}
      {selectedDataset && !defLoading && (defAttributes.length > 0 || defMetrics.length > 0) && (
        <div className="px-6 py-4 border-b border-[#1e2d50] bg-[#0d0d1a] overflow-auto">
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            {/* Box 1: Columns */}
            <div className="bg-[#16213e] rounded-xl p-4 border border-[#1e2d50]">
              <h3 className="text-xs text-white uppercase tracking-wider font-semibold mb-3 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-blue-500" />
                Columns
              </h3>
              <RequestedObjects
                attributes={defAttributes}
                metrics={defMetrics}
                selectedAttrIds={selectedAttrIds}
                selectedMetricIds={selectedMetricIds}
                onToggleAttr={toggleAttr}
                onToggleMetric={toggleMetric}
                onSelectAllAttrs={() => setSelectedAttrIds(new Set(defAttributes.map(a => a.id)))}
                onDeselectAllAttrs={() => setSelectedAttrIds(new Set())}
                onSelectAllMetrics={() => setSelectedMetricIds(new Set(defMetrics.map(m => m.id)))}
                onDeselectAllMetrics={() => setSelectedMetricIds(new Set())}
              />
            </div>

            {/* Box 2: View Filters */}
            <div className="bg-[#16213e] rounded-xl p-4 border border-[#1e2d50]">
              <h3 className="text-xs text-white uppercase tracking-wider font-semibold mb-3 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-yellow-500" />
                View Filters
              </h3>
              <FilterBuilder
                attributes={defAttributes}
                metrics={defMetrics}
                filters={filters}
                onFiltersChange={setFilters}
                availableElements={filterElements}
                onLoadElements={loadFilterElements}
                elementsLoading={filterElementsLoading}
              />
            </div>

            {/* Box 3: Metric Limits */}
            <div className="bg-[#16213e] rounded-xl p-4 border border-[#1e2d50]">
              <h3 className="text-xs text-white uppercase tracking-wider font-semibold mb-3 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-green-500" />
                Metric Limits
              </h3>
              <MetricLimitsComponent
                metrics={defMetrics}
                limits={metricLimits}
                onLimitsChange={setMetricLimits}
                selectedMetricIds={selectedMetricIds}
              />
            </div>

            {/* Box 4: Sorting */}
            <div className="bg-[#16213e] rounded-xl p-4 border border-[#1e2d50]">
              <h3 className="text-xs text-white uppercase tracking-wider font-semibold mb-3 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-purple-500" />
                Sorting
              </h3>
              <SortBuilder
                attributes={defAttributes}
                metrics={defMetrics}
                sorting={sorting}
                onSortingChange={setSorting}
                selectedAttrIds={selectedAttrIds}
                selectedMetricIds={selectedMetricIds}
              />
            </div>
          </div>
          <div className="flex justify-end mt-4">
            <button
              onClick={() => execute(0)}
              disabled={loading}
              className="px-5 py-2 bg-gradient-to-r from-red-600 to-red-700 hover:from-red-700 hover:to-red-800 text-white text-sm font-medium rounded-lg transition-all disabled:opacity-50 flex items-center gap-2"
            >
              <VscPlay /> Apply &amp; Execute
            </button>
          </div>
        </div>
      )}

      {/* Definition loading */}
      {defLoading && (
        <div className="px-6 py-8 text-center text-gray-400">
          <div className="animate-spin inline-block w-5 h-5 border-2 border-gray-600 border-t-red-500 rounded-full mb-2" />
          <p className="text-xs">Loading definition...</p>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="mx-6 mt-4 p-4 bg-red-900/30 border border-red-800 rounded-lg text-red-300 text-sm">
          {error}
          <button onClick={() => execute(0)} className="ml-3 underline hover:text-red-200">Retry</button>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex-1 flex items-center justify-center text-gray-400">
          <div className="text-center">
            <div className="animate-spin inline-block w-6 h-6 border-2 border-gray-600 border-t-red-500 rounded-full mb-3" />
            <p className="text-sm">Executing...</p>
          </div>
        </div>
      )}

      {/* Data table */}
      {!loading && columns.length > 0 && (
        <div className="flex-1 overflow-auto">
          <table className="w-full text-sm border-collapse">
            <thead className="sticky top-0 z-10">
              <tr>
                {columns.map((col, i) => (
                  <th key={i} className="bg-[#16213e] text-left px-3 py-2.5 text-xs font-medium text-gray-300 border-b border-[#1e2d50] whitespace-nowrap">
                    <div>{col.name}</div>
                    <div className={`text-[10px] font-normal ${col.type === 'attribute' ? 'text-blue-600' : 'text-green-600'}`}>
                      {col.type === 'attribute' ? 'Attribute' : 'Metric'}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri} className="hover:bg-[#16213e]/50 transition-colors">
                  {row.map((cell, ci) => (
                    <td key={ci} className="px-3 py-2 text-gray-300 border-b border-[#1a1a2e] whitespace-nowrap">
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {!loading && columns.length > 0 && totalPages > 1 && (
        <div className="px-4 py-2.5 border-t border-[#1e2d50] bg-[#16213e] flex items-center justify-between">
          <span className="text-xs text-gray-500">
            Showing {pageOffset + 1}–{Math.min(pageOffset + PAGE_SIZE, totalRows)} of {totalRows}
          </span>
          <div className="flex items-center gap-1">
            <button onClick={() => fetchPage(0)} disabled={currentPage <= 1} className="px-2 py-1 text-xs text-gray-400 hover:text-white disabled:opacity-30">First</button>
            <button onClick={() => fetchPage(pageOffset - PAGE_SIZE)} disabled={currentPage <= 1} className="px-2 py-1 text-gray-400 hover:text-white disabled:opacity-30"><VscArrowSmallLeft /></button>
            <span className="text-xs text-gray-300 px-2">Page {currentPage} / {totalPages}</span>
            <button onClick={() => fetchPage(pageOffset + PAGE_SIZE)} disabled={currentPage >= totalPages} className="px-2 py-1 text-gray-400 hover:text-white disabled:opacity-30"><VscArrowSmallRight /></button>
            <button onClick={() => fetchPage((totalPages - 1) * PAGE_SIZE)} disabled={currentPage >= totalPages} className="px-2 py-1 text-xs text-gray-400 hover:text-white disabled:opacity-30">Last</button>
          </div>
        </div>
      )}

      {/* Empty states */}
      {!loading && !error && columns.length === 0 && !defLoading && (
        <div className="flex-1 flex flex-col items-center justify-center text-gray-500 p-6">
          {!selectedDataset ? (
            <>
              <p className="mb-2 text-lg">Select a Dataset</p>
              <p className="text-xs text-gray-600">Choose a report or cube from the dropdown above, configure query options, then click Execute.</p>
            </>
          ) : (
            <>
              <p className="mb-2">Ready to execute</p>
              <p className="text-xs text-gray-600">Configure columns, filters, sorting, and metric limits above, then click Execute.</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
