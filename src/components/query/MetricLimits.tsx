import { useState } from 'react';
import { VscAdd, VscTrash } from 'react-icons/vsc';
import type { GridMetric } from '../../api/v2GridParser';

const OPERATORS = [
  { value: 'Equals', label: '=' },
  { value: 'NotEqual', label: '!=' },
  { value: 'Greater', label: '>' },
  { value: 'Less', label: '<' },
  { value: 'GreaterEqual', label: '>=' },
  { value: 'LessEqual', label: '<=' },
];

export interface MetricLimit {
  id: string;
  metricId: string;
  metricName: string;
  operator: string;
  value: string;
}

interface Props {
  metrics: GridMetric[];
  limits: MetricLimit[];
  onLimitsChange: (limits: MetricLimit[]) => void;
  selectedMetricIds: Set<string>;
}

export function buildMetricLimits(limits: MetricLimit[]): Record<string, any> | undefined {
  if (limits.length === 0) return undefined;
  const result: Record<string, any> = {};
  for (const lim of limits) {
    result[lim.metricId] = {
      operator: lim.operator,
      operands: [
        { type: 'metric', id: lim.metricId, name: lim.metricName },
        { type: 'constant', dataType: 'Real', value: lim.value },
      ],
    };
  }
  return result;
}

export default function MetricLimits({ metrics, limits, onLimitsChange, selectedMetricIds }: Props) {
  const [editMetric, setEditMetric] = useState('');
  const [editOp, setEditOp] = useState('Greater');
  const [editValue, setEditValue] = useState('');

  const availableMetrics = metrics.filter(m => selectedMetricIds.has(m.id));

  function addLimit() {
    const metric = metrics.find(m => m.id === editMetric);
    if (!metric || !editValue) return;
    onLimitsChange([...limits, {
      id: crypto.randomUUID(),
      metricId: metric.id,
      metricName: metric.name,
      operator: editOp,
      value: editValue,
    }]);
    setEditValue('');
  }

  function removeLimit(id: string) {
    onLimitsChange(limits.filter(l => l.id !== id));
  }

  return (
    <div>
      {/* Existing limits */}
      {limits.length > 0 && (
        <div className="space-y-1.5 mb-3">
          {limits.map(l => (
            <div key={l.id} className="flex items-center gap-2 bg-[#16213e] px-3 py-1.5 rounded border border-[#1e2d50]">
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-900/30 text-green-400">MTR</span>
              <span className="text-xs text-gray-200">{l.metricName}</span>
              <span className="text-xs text-yellow-400 font-mono">{l.operator}</span>
              <span className="text-xs text-gray-400 font-mono">{l.value}</span>
              <button onClick={() => removeLimit(l.id)} className="ml-auto text-gray-600 hover:text-red-400">
                <VscTrash className="text-xs" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Add limit form */}
      <div className="flex gap-2 items-center">
        <select
          value={editMetric}
          onChange={e => setEditMetric(e.target.value)}
          className="flex-1 px-2 py-1.5 bg-[#0f0f1a] border border-[#2a2a4a] rounded text-xs text-white focus:outline-none focus:border-red-500"
        >
          <option value="">Select metric...</option>
          {availableMetrics.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
        <select
          value={editOp}
          onChange={e => setEditOp(e.target.value)}
          className="w-20 px-2 py-1.5 bg-[#0f0f1a] border border-[#2a2a4a] rounded text-xs text-white focus:outline-none focus:border-red-500"
        >
          {OPERATORS.map(op => <option key={op.value} value={op.value}>{op.label}</option>)}
        </select>
        <input
          type="text"
          value={editValue}
          onChange={e => setEditValue(e.target.value)}
          placeholder="Value"
          className="w-24 px-2 py-1.5 bg-[#0f0f1a] border border-[#2a2a4a] rounded text-xs text-white focus:outline-none focus:border-red-500"
          onKeyDown={e => e.key === 'Enter' && addLimit()}
        />
        <button
          onClick={addLimit}
          disabled={!editMetric || !editValue}
          className="flex items-center gap-1 px-2 py-1.5 bg-[#1e2d50] hover:bg-[#2a3d60] text-gray-300 text-xs rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <VscAdd />
        </button>
      </div>

      {availableMetrics.length === 0 && (
        <p className="text-xs text-gray-600 mt-2">Select metrics in Columns first</p>
      )}
    </div>
  );
}
