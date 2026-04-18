import type { GridAttribute, GridMetric } from '../../api/v2GridParser';

interface Props {
  attributes: GridAttribute[];
  metrics: GridMetric[];
  selectedAttrIds: Set<string>;
  selectedMetricIds: Set<string>;
  onToggleAttr: (id: string) => void;
  onToggleMetric: (id: string) => void;
  onSelectAllAttrs: () => void;
  onDeselectAllAttrs: () => void;
  onSelectAllMetrics: () => void;
  onDeselectAllMetrics: () => void;
}

export default function RequestedObjects({
  attributes, metrics,
  selectedAttrIds, selectedMetricIds,
  onToggleAttr, onToggleMetric,
  onSelectAllAttrs, onDeselectAllAttrs,
  onSelectAllMetrics, onDeselectAllMetrics,
}: Props) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {/* Attributes */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h4 className="text-xs text-blue-400 uppercase tracking-wider font-medium">
            Attributes ({selectedAttrIds.size}/{attributes.length})
          </h4>
          <div className="flex gap-2">
            <button onClick={onSelectAllAttrs} className="text-[10px] text-gray-500 hover:text-white">All</button>
            <button onClick={onDeselectAllAttrs} className="text-[10px] text-gray-500 hover:text-white">None</button>
          </div>
        </div>
        <div className="space-y-1 max-h-48 overflow-auto">
          {attributes.length === 0 && <p className="text-xs text-gray-600">No attributes</p>}
          {attributes.map(a => (
            <label key={a.id} className="flex items-center gap-2 text-xs cursor-pointer hover:bg-[#1a2a4a] px-2 py-1 rounded">
              <input
                type="checkbox"
                checked={selectedAttrIds.has(a.id)}
                onChange={() => onToggleAttr(a.id)}
                className="accent-blue-500"
              />
              <span className={selectedAttrIds.has(a.id) ? 'text-gray-200' : 'text-gray-500'}>{a.name}</span>
              {a.forms.length > 1 && (
                <span className="text-[10px] text-gray-600">({a.forms.map(f => f.name).join(', ')})</span>
              )}
            </label>
          ))}
        </div>
      </div>

      {/* Metrics */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h4 className="text-xs text-green-400 uppercase tracking-wider font-medium">
            Metrics ({selectedMetricIds.size}/{metrics.length})
          </h4>
          <div className="flex gap-2">
            <button onClick={onSelectAllMetrics} className="text-[10px] text-gray-500 hover:text-white">All</button>
            <button onClick={onDeselectAllMetrics} className="text-[10px] text-gray-500 hover:text-white">None</button>
          </div>
        </div>
        <div className="space-y-1 max-h-48 overflow-auto">
          {metrics.length === 0 && <p className="text-xs text-gray-600">No metrics</p>}
          {metrics.map(m => (
            <label key={m.id} className="flex items-center gap-2 text-xs cursor-pointer hover:bg-[#1a2a4a] px-2 py-1 rounded">
              <input
                type="checkbox"
                checked={selectedMetricIds.has(m.id)}
                onChange={() => onToggleMetric(m.id)}
                className="accent-green-500"
              />
              <span className={selectedMetricIds.has(m.id) ? 'text-gray-200' : 'text-gray-500'}>{m.name}</span>
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}
