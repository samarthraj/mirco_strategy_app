import { useState, useEffect, useCallback } from 'react';
import {
  VscArrowLeft, VscInfo, VscJson, VscSymbolProperty,
  VscChevronDown, VscChevronRight, VscPlay, VscArrowSmallLeft, VscArrowSmallRight,
} from 'react-icons/vsc';
import {
  getReportDefinition, getCubeDefinition,
  getV2ReportDefinition, getV2CubeDefinition,
  createV2ReportInstance, createV2CubeInstance,
  getV2ReportInstanceData, getV2CubeInstanceData,
  getAttributeElements, getObjectInfo, getObjectDependencies,
} from '../api/mstrClient';
import type { V2InstanceBody } from '../api/mstrClient';
import { parseV2Definition, parseV2Grid } from '../api/v2GridParser';
import type { GridAttribute, GridMetric, ParsedColumn } from '../api/v2GridParser';
import { useApp } from '../context/AppContext';
import RequestedObjects from './query/RequestedObjects';
import FilterBuilder, { buildViewFilter } from './query/FilterBuilder';
import type { FilterExpression } from './query/FilterBuilder';
import SortBuilder, { buildSorting } from './query/SortBuilder';
import type { SortCriteria } from './query/SortBuilder';
import MetricLimitsComponent, { buildMetricLimits } from './query/MetricLimits';
import type { MetricLimit } from './query/MetricLimits';

// MicroStrategy object type names
const OBJECT_TYPES: Record<number, string> = {
  1: 'Filter', 2: 'Template', 3: 'Report', 4: 'Metric',
  6: 'Prompt', 7: 'Attribute', 8: 'Folder', 10: 'Table',
  12: 'Attribute Form', 13: 'Column', 14: 'Fact', 15: 'Function',
  21: 'Cube', 34: 'Dossier', 39: 'Document', 44: 'Security Filter',
  47: 'Consolidation', 55: 'Dashboard',
};

const PAGE_SIZE = 100;

export default function DataViewer() {
  const { selectedObjectId, selectedObjectType, currentView, setCurrentView } = useApp();
  const isReport = selectedObjectType === 3;
  const typeLabel = isReport ? 'Report' : 'Cube';
  const objectType = isReport ? 'reports' as const : 'cubes' as const;

  // --- Definition state ---
  const [defAttributes, setDefAttributes] = useState<GridAttribute[]>([]);
  const [defMetrics, setDefMetrics] = useState<GridMetric[]>([]);
  const [definition, setDefinition] = useState<any>(null);
  const [defLoading, setDefLoading] = useState(false);

  // --- Query options state ---
  const [selectedAttrIds, setSelectedAttrIds] = useState<Set<string>>(new Set());
  const [selectedMetricIds, setSelectedMetricIds] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState<FilterExpression[]>([]);
  const [sorting, setSorting] = useState<SortCriteria[]>([]);
  const [metricLimits, setMetricLimits] = useState<MetricLimit[]>([]);

  // --- Filter elements state (for "select in list") ---
  const [filterElements, setFilterElements] = useState<{ id: string; name: string }[]>([]);
  const [filterElementsLoading, setFilterElementsLoading] = useState(false);

  // --- Instance / data state ---
  const [instanceId, setInstanceId] = useState<string | null>(null);
  const [columns, setColumns] = useState<ParsedColumn[]>([]);
  const [rows, setRows] = useState<string[][]>([]);
  const [totalRows, setTotalRows] = useState(0);
  const [pageOffset, setPageOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [rawResponse, setRawResponse] = useState<any>(null);

  // --- Panel toggles ---
  const [showQueryOptions, setShowQueryOptions] = useState(false);
  const [activeTab, setActiveTab] = useState<'columns' | 'filters' | 'sorting' | 'limits'>('columns');
  const [showRaw, setShowRaw] = useState(false);
  const [showDefinition, setShowDefinition] = useState(false);
  const [showMetadata, setShowMetadata] = useState(false);
  const [metadata, setMetadata] = useState<any>(null);
  const [metadataLoading, setMetadataLoading] = useState(false);

  // --- Load V2 definition on mount, then auto-execute ---
  useEffect(() => {
    if (selectedObjectId) {
      resetState();
      loadAndExecute();
    }
  }, [selectedObjectId]);

  async function loadAndExecute() {
    await loadV2Definition();
    // executeInstance will use the latest state via buildInstanceBody,
    // but since we just loaded with all selected, a plain execute works
    if (!selectedObjectId) return;
    setLoading(true);
    setError('');
    try {
      const createFn = isReport ? createV2ReportInstance : createV2CubeInstance;
      const data = await createFn(selectedObjectId, {}, 0, PAGE_SIZE);
      setRawResponse(data);
      const instId = data.instanceId || data.id;
      setInstanceId(instId);
      const grid = parseV2Grid(data);
      setColumns(grid.columns);
      setRows(grid.rows);
      setTotalRows(grid.totalRows);
      setPageOffset(0);
    } catch (err: any) {
      const resp = err.response?.data;
      setRawResponse(resp || null);
      setError(resp?.message || err.message || `Failed to execute ${typeLabel.toLowerCase()}`);
    } finally {
      setLoading(false);
    }
  }

  function resetState() {
    setDefAttributes([]);
    setDefMetrics([]);
    setDefinition(null);
    setSelectedAttrIds(new Set());
    setSelectedMetricIds(new Set());
    setFilters([]);
    setSorting([]);
    setMetricLimits([]);
    setInstanceId(null);
    setColumns([]);
    setRows([]);
    setTotalRows(0);
    setPageOffset(0);
    setError('');
    setRawResponse(null);
    setMetadata(null);
    setShowMetadata(false);
  }

  async function loadV2Definition() {
    setDefLoading(true);
    try {
      const fn = isReport ? getV2ReportDefinition : getV2CubeDefinition;
      const data = await fn(selectedObjectId!);
      setDefinition(data);
      const { attributes, metrics } = parseV2Definition(data);
      setDefAttributes(attributes);
      setDefMetrics(metrics);
      setSelectedAttrIds(new Set(attributes.map(a => a.id)));
      setSelectedMetricIds(new Set(metrics.map(m => m.id)));
    } catch {
      // Fall back to v1 definition
      try {
        const fn = isReport ? getReportDefinition : getCubeDefinition;
        const data = await fn(selectedObjectId!);
        setDefinition(data);
        const { attributes, metrics } = parseV2Definition(data);
        setDefAttributes(attributes);
        setDefMetrics(metrics);
        setSelectedAttrIds(new Set(attributes.map(a => a.id)));
        setSelectedMetricIds(new Set(metrics.map(m => m.id)));
      } catch {
        // will show empty state
      }
    } finally {
      setDefLoading(false);
    }
  }

  // --- Build POST body from query options ---
  function buildInstanceBody(): V2InstanceBody {
    const body: V2InstanceBody = {};

    // requestedObjects — only include if user deselected something
    const allAttrsSelected = selectedAttrIds.size === defAttributes.length;
    const allMetricsSelected = selectedMetricIds.size === defMetrics.length;
    if (!allAttrsSelected || !allMetricsSelected) {
      body.requestedObjects = {
        attributes: defAttributes.filter(a => selectedAttrIds.has(a.id)).map(a => ({ id: a.id })),
        metrics: defMetrics.filter(m => selectedMetricIds.has(m.id)).map(m => ({ id: m.id })),
      };
    }

    const viewFilter = buildViewFilter(filters);
    if (viewFilter) body.viewFilter = viewFilter;

    const ml = buildMetricLimits(metricLimits);
    if (ml) body.metricLimits = ml;

    const sort = buildSorting(sorting);
    if (sort) body.sorting = sort;

    return body;
  }

  // --- Execute ---
  const executeInstance = useCallback(async (offset = 0) => {
    if (!selectedObjectId) return;
    setLoading(true);
    setError('');

    try {
      const body = buildInstanceBody();
      const createFn = isReport ? createV2ReportInstance : createV2CubeInstance;
      const data = await createFn(selectedObjectId, body, offset, PAGE_SIZE);
      setRawResponse(data);

      const instId = data.instanceId || data.id;
      setInstanceId(instId);

      const grid = parseV2Grid(data);
      setColumns(grid.columns);
      setRows(grid.rows);
      setTotalRows(grid.totalRows);
      setPageOffset(offset);
    } catch (err: any) {
      const resp = err.response?.data;
      setRawResponse(resp || null);
      setError(resp?.message || err.message || `Failed to execute ${typeLabel.toLowerCase()}`);
    } finally {
      setLoading(false);
    }
  }, [selectedObjectId, isReport, selectedAttrIds, selectedMetricIds, filters, sorting, metricLimits, defAttributes, defMetrics]);

  // --- Pagination ---
  async function fetchPage(offset: number) {
    if (!selectedObjectId || !instanceId) return;
    setLoading(true);
    setError('');

    try {
      const getFn = isReport ? getV2ReportInstanceData : getV2CubeInstanceData;
      const data = await getFn(selectedObjectId, instanceId, offset, PAGE_SIZE);
      setRawResponse(data);
      const grid = parseV2Grid(data);
      setColumns(grid.columns);
      setRows(grid.rows);
      setTotalRows(grid.totalRows);
      setPageOffset(offset);
    } catch (err: any) {
      // Instance may have expired — re-execute
      if (err.response?.status === 404) {
        await executeInstance(offset);
        return;
      }
      setError(err.response?.data?.message || err.message || 'Failed to fetch page');
    } finally {
      setLoading(false);
    }
  }

  const currentPage = Math.floor(pageOffset / PAGE_SIZE) + 1;
  const totalPages = Math.ceil(totalRows / PAGE_SIZE) || 1;

  // --- Load attribute elements for filter "select in list" ---
  async function loadFilterElements(attributeId: string) {
    if (!instanceId) {
      setFilterElements([]);
      return;
    }
    setFilterElementsLoading(true);
    try {
      const data = await getAttributeElements(objectType, selectedObjectId!, instanceId, attributeId);
      const elements = (Array.isArray(data) ? data : data?.elements || data || []).map((el: any) => ({
        id: el.id,
        name: el.formValues?.[0] || el.name || el.id,
      }));
      setFilterElements(elements);
    } catch {
      setFilterElements([]);
    } finally {
      setFilterElementsLoading(false);
    }
  }

  // --- Metadata (object info + dependencies) ---
  async function loadMetadata() {
    if (metadata) { setShowMetadata(!showMetadata); return; }
    setMetadataLoading(true);
    setShowMetadata(true);
    try {
      const [objRes, depsRes] = await Promise.allSettled([
        getObjectInfo(selectedObjectId!, isReport ? 3 : 21),
        getObjectDependencies(selectedObjectId!),
      ]);
      const objData = objRes.status === 'fulfilled' ? objRes.value : null;
      const depsData = depsRes.status === 'fulfilled' ? depsRes.value : null;
      const depsList = (Array.isArray(depsData) ? depsData : depsData?.dependencies || []).map((d: any) => ({
        id: d.id || d.objectId, name: d.name, type: d.type,
        typeName: OBJECT_TYPES[d.type] || `Type ${d.type}`,
      }));
      setMetadata({ objectInfo: objData, dependencies: depsList });
    } catch (err: any) {
      setMetadata({ error: err.message });
    } finally {
      setMetadataLoading(false);
    }
  }

  // --- Column selection helpers ---
  function toggleAttr(id: string) {
    setSelectedAttrIds(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  }
  function toggleMetric(id: string) {
    setSelectedMetricIds(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  }

  function handleBack() {
    setCurrentView(currentView === 'report' ? 'reports' : 'browser');
  }

  if (!selectedObjectId) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-500">
        <p>Select a report or cube to view its data</p>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Toolbar */}
      <div className="px-4 py-3 border-b border-[#1e2d50] bg-[#16213e] flex items-center gap-3">
        <button onClick={handleBack} className="text-gray-400 hover:text-white transition-colors">
          <VscArrowLeft className="text-lg" />
        </button>
        <div className="flex-1 min-w-0">
          <h2 className="text-white font-medium text-sm truncate">{definition?.name || selectedObjectId}</h2>
          <p className="text-xs text-gray-500">{typeLabel} &middot; {totalRows} rows</p>
        </div>

        <button
          onClick={() => setShowQueryOptions(!showQueryOptions)}
          className={`px-3 py-1.5 rounded text-xs transition-colors flex items-center gap-1 ${showQueryOptions ? 'bg-red-600 text-white' : 'bg-[#0f0f1a] text-gray-400 hover:text-white'}`}
        >
          Query Options {showQueryOptions ? <VscChevronDown className="text-[10px]" /> : <VscChevronRight className="text-[10px]" />}
        </button>

        <button onClick={() => executeInstance(0)} disabled={loading || defLoading} className="px-4 py-1.5 bg-gradient-to-r from-red-600 to-red-700 hover:from-red-700 hover:to-red-800 text-white text-xs font-medium rounded transition-all disabled:opacity-50 flex items-center gap-1.5">
          <VscPlay /> Execute
        </button>

        <button onClick={loadMetadata} disabled={metadataLoading} className={`px-3 py-1.5 rounded text-xs transition-colors ${showMetadata ? 'bg-blue-600 text-white' : 'bg-[#0f0f1a] text-gray-400 hover:text-white'}`}>
          <VscSymbolProperty className="inline mr-1" /> {metadataLoading ? '...' : 'Metadata'}
        </button>
        <button onClick={() => setShowRaw(!showRaw)} className={`px-3 py-1.5 rounded text-xs transition-colors ${showRaw ? 'bg-blue-600 text-white' : 'bg-[#0f0f1a] text-gray-400 hover:text-white'}`}>
          <VscJson className="inline mr-1" /> Raw
        </button>
        <button onClick={() => setShowDefinition(!showDefinition)} className={`px-3 py-1.5 rounded text-xs transition-colors ${showDefinition ? 'bg-blue-600 text-white' : 'bg-[#0f0f1a] text-gray-400 hover:text-white'}`}>
          <VscInfo className="inline mr-1" /> Def
        </button>
      </div>

      {/* Query Options Panel */}
      {showQueryOptions && (
        <div className="border-b border-[#1e2d50] bg-[#0a0a15]">
          {/* Tabs */}
          <div className="flex border-b border-[#1e2d50]">
            {(['columns', 'filters', 'sorting', 'limits'] as const).map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-4 py-2 text-xs font-medium transition-colors ${activeTab === tab ? 'text-white border-b-2 border-red-500 bg-[#16213e]' : 'text-gray-500 hover:text-gray-300'}`}
              >
                {tab === 'columns' && `Columns (${selectedAttrIds.size + selectedMetricIds.size})`}
                {tab === 'filters' && `Filters (${filters.length})`}
                {tab === 'sorting' && `Sorting (${sorting.length})`}
                {tab === 'limits' && `Metric Limits (${metricLimits.length})`}
              </button>
            ))}
          </div>
          <div className="p-4 max-h-64 overflow-auto">
            {defLoading ? (
              <p className="text-xs text-gray-500">Loading definition...</p>
            ) : (
              <>
                {activeTab === 'columns' && (
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
                )}
                {activeTab === 'filters' && (
                  <FilterBuilder
                    attributes={defAttributes}
                    metrics={defMetrics}
                    filters={filters}
                    onFiltersChange={setFilters}
                    availableElements={filterElements}
                    onLoadElements={loadFilterElements}
                    elementsLoading={filterElementsLoading}
                  />
                )}
                {activeTab === 'sorting' && (
                  <SortBuilder
                    attributes={defAttributes}
                    metrics={defMetrics}
                    sorting={sorting}
                    onSortingChange={setSorting}
                    selectedAttrIds={selectedAttrIds}
                    selectedMetricIds={selectedMetricIds}
                  />
                )}
                {activeTab === 'limits' && (
                  <MetricLimitsComponent
                    metrics={defMetrics}
                    limits={metricLimits}
                    onLimitsChange={setMetricLimits}
                    selectedMetricIds={selectedMetricIds}
                  />
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* Metadata panel */}
      {showMetadata && metadata && (
        <div className="border-b border-[#1e2d50] bg-[#0a0a15] max-h-[40vh] overflow-auto p-4">
          {metadata.error ? (
            <p className="text-xs text-red-400">{metadata.error}</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {metadata.dependencies?.length > 0 && (
                <div className="bg-[#16213e] rounded-lg p-3 border border-[#1e2d50]">
                  <h4 className="text-xs text-purple-400 uppercase tracking-wider font-medium mb-2">
                    Dependencies ({metadata.dependencies.length})
                  </h4>
                  <div className="space-y-1.5 max-h-48 overflow-auto">
                    {metadata.dependencies.map((d: any) => (
                      <div key={d.id} className="text-xs flex items-start gap-2">
                        <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-[#0f0f1a] text-gray-400 border border-[#2a2a4a]">{d.typeName}</span>
                        <div className="min-w-0">
                          <span className="text-gray-200">{d.name}</span>
                          <div className="text-[10px] text-gray-600 font-mono truncate">{d.id}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {metadata.objectInfo && (
                <div className="bg-[#16213e] rounded-lg p-3 border border-[#1e2d50]">
                  <h4 className="text-xs text-gray-400 uppercase tracking-wider font-medium mb-2">Object Info</h4>
                  <pre className="text-xs text-gray-300 whitespace-pre-wrap max-h-48 overflow-auto">
                    {JSON.stringify(metadata.objectInfo, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Definition panel */}
      {showDefinition && definition && (
        <div className="p-4 border-b border-[#1e2d50] bg-[#0f0f1a] max-h-60 overflow-auto">
          <pre className="text-xs text-gray-300 whitespace-pre-wrap">{JSON.stringify(definition, null, 2)}</pre>
        </div>
      )}

      {/* Raw response panel */}
      {showRaw && rawResponse && (
        <div className="p-4 border-b border-[#1e2d50] bg-[#0a0a15] max-h-80 overflow-auto">
          <h4 className="text-xs text-gray-500 uppercase tracking-wider mb-2">Raw API Response</h4>
          <pre className="text-xs text-gray-300 whitespace-pre-wrap">{JSON.stringify(rawResponse, null, 2)}</pre>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="m-4 p-4 bg-red-900/30 border border-red-800 rounded-lg text-red-300 text-sm">
          {error}
          <button onClick={() => executeInstance(0)} className="ml-3 underline hover:text-red-200">Retry</button>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex-1 flex items-center justify-center text-gray-400">
          <div className="text-center">
            <div className="animate-spin inline-block w-6 h-6 border-2 border-gray-600 border-t-red-500 rounded-full mb-3" />
            <p>Executing {typeLabel.toLowerCase()}...</p>
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
                  <th key={i} className="bg-[#16213e] text-left px-3 py-2 text-xs font-medium text-gray-300 border-b border-[#1e2d50] whitespace-nowrap">
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
        <div className="px-4 py-2 border-t border-[#1e2d50] bg-[#16213e] flex items-center justify-between">
          <span className="text-xs text-gray-500">
            Showing {pageOffset + 1}-{Math.min(pageOffset + PAGE_SIZE, totalRows)} of {totalRows}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => fetchPage(0)}
              disabled={currentPage <= 1}
              className="px-2 py-1 text-xs text-gray-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
            >
              First
            </button>
            <button
              onClick={() => fetchPage(pageOffset - PAGE_SIZE)}
              disabled={currentPage <= 1}
              className="px-2 py-1 text-gray-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <VscArrowSmallLeft />
            </button>
            <span className="text-xs text-gray-300 px-2">
              Page {currentPage} / {totalPages}
            </span>
            <button
              onClick={() => fetchPage(pageOffset + PAGE_SIZE)}
              disabled={currentPage >= totalPages}
              className="px-2 py-1 text-gray-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <VscArrowSmallRight />
            </button>
            <button
              onClick={() => fetchPage((totalPages - 1) * PAGE_SIZE)}
              disabled={currentPage >= totalPages}
              className="px-2 py-1 text-xs text-gray-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
            >
              Last
            </button>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && columns.length === 0 && !defLoading && (
        <div className="flex-1 flex flex-col items-center justify-center text-gray-500 p-6">
          {rawResponse ? (
            <>
              <p className="mb-2">No tabular data returned</p>
              <p className="text-xs text-gray-600">Click "Raw" to inspect the response.</p>
            </>
          ) : (
            <>
              <p className="mb-2">Click "Execute" to run this {typeLabel.toLowerCase()}</p>
              <p className="text-xs text-gray-600">Use "Query Options" to select columns, add filters, sorting, and metric limits before executing.</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
