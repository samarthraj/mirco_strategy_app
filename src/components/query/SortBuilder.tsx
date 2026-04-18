import { VscTrash, VscArrowUp, VscArrowDown } from 'react-icons/vsc';
import type { GridAttribute, GridMetric } from '../../api/v2GridParser';

export interface SortCriteria {
  id: string;
  type: 'attribute' | 'metric';
  targetId: string;
  targetName: string;
  order: 'ascending' | 'descending';
}

interface Props {
  attributes: GridAttribute[];
  metrics: GridMetric[];
  sorting: SortCriteria[];
  onSortingChange: (sorting: SortCriteria[]) => void;
  selectedAttrIds: Set<string>;
  selectedMetricIds: Set<string>;
}

export function buildSorting(sorting: SortCriteria[]): any[] | undefined {
  if (sorting.length === 0) return undefined;
  return sorting.map(s => {
    if (s.type === 'attribute') {
      return { type: 'attribute', attribute: { id: s.targetId, name: s.targetName }, order: s.order };
    }
    return { type: 'metric', metric: { id: s.targetId, name: s.targetName }, order: s.order };
  });
}

export default function SortBuilder({ attributes, metrics, sorting, onSortingChange, selectedAttrIds, selectedMetricIds }: Props) {
  function addSort(targetId: string, type: 'attribute' | 'metric', name: string) {
    if (sorting.some(s => s.targetId === targetId)) return;
    onSortingChange([...sorting, {
      id: crypto.randomUUID(),
      type,
      targetId,
      targetName: name,
      order: 'ascending',
    }]);
  }

  function removeSort(id: string) {
    onSortingChange(sorting.filter(s => s.id !== id));
  }

  function toggleOrder(id: string) {
    onSortingChange(sorting.map(s =>
      s.id === id ? { ...s, order: s.order === 'ascending' ? 'descending' : 'ascending' } : s
    ));
  }

  function moveUp(idx: number) {
    if (idx === 0) return;
    const next = [...sorting];
    [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
    onSortingChange(next);
  }

  function moveDown(idx: number) {
    if (idx >= sorting.length - 1) return;
    const next = [...sorting];
    [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
    onSortingChange(next);
  }

  // Only show selected attributes/metrics as sortable
  const availableAttrs = attributes.filter(a => selectedAttrIds.has(a.id) && !sorting.some(s => s.targetId === a.id));
  const availableMetrics = metrics.filter(m => selectedMetricIds.has(m.id) && !sorting.some(s => s.targetId === m.id));

  return (
    <div>
      {/* Existing sort criteria */}
      {sorting.length > 0 && (
        <div className="space-y-1.5 mb-3">
          {sorting.map((s, idx) => (
            <div key={s.id} className="flex items-center gap-2 bg-[#16213e] px-3 py-1.5 rounded border border-[#1e2d50]">
              <span className="text-gray-600 text-xs font-mono w-4">{idx + 1}</span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${s.type === 'attribute' ? 'bg-blue-900/30 text-blue-400' : 'bg-green-900/30 text-green-400'}`}>
                {s.type === 'attribute' ? 'ATTR' : 'MTR'}
              </span>
              <span className="text-xs text-gray-200 flex-1">{s.targetName}</span>
              <button
                onClick={() => toggleOrder(s.id)}
                className={`text-xs px-2 py-0.5 rounded font-mono ${s.order === 'ascending' ? 'text-blue-400 bg-blue-900/20' : 'text-orange-400 bg-orange-900/20'}`}
              >
                {s.order === 'ascending' ? 'ASC' : 'DESC'}
              </button>
              <button onClick={() => moveUp(idx)} disabled={idx === 0} className="text-gray-600 hover:text-white disabled:opacity-30">
                <VscArrowUp className="text-xs" />
              </button>
              <button onClick={() => moveDown(idx)} disabled={idx >= sorting.length - 1} className="text-gray-600 hover:text-white disabled:opacity-30">
                <VscArrowDown className="text-xs" />
              </button>
              <button onClick={() => removeSort(s.id)} className="text-gray-600 hover:text-red-400">
                <VscTrash className="text-xs" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Add sort dropdown */}
      {(availableAttrs.length > 0 || availableMetrics.length > 0) && (
        <div className="flex gap-2 items-center">
          <select
            defaultValue=""
            onChange={e => {
              const val = e.target.value;
              if (!val) return;
              const attr = attributes.find(a => a.id === val);
              if (attr) { addSort(attr.id, 'attribute', attr.name); e.target.value = ''; return; }
              const metric = metrics.find(m => m.id === val);
              if (metric) { addSort(metric.id, 'metric', metric.name); e.target.value = ''; }
            }}
            className="flex-1 px-2 py-1.5 bg-[#0f0f1a] border border-[#2a2a4a] rounded text-xs text-white focus:outline-none focus:border-red-500"
          >
            <option value="">Add sort by...</option>
            {availableAttrs.length > 0 && (
              <optgroup label="Attributes">
                {availableAttrs.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
              </optgroup>
            )}
            {availableMetrics.length > 0 && (
              <optgroup label="Metrics">
                {availableMetrics.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
              </optgroup>
            )}
          </select>
        </div>
      )}

      {sorting.length === 0 && availableAttrs.length === 0 && availableMetrics.length === 0 && (
        <p className="text-xs text-gray-600">Select attributes or metrics first</p>
      )}
    </div>
  );
}
