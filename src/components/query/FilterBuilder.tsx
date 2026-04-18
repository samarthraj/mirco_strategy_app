import { useState } from 'react';
import { VscAdd, VscTrash } from 'react-icons/vsc';
import type { GridAttribute, GridMetric } from '../../api/v2GridParser';

const OPERATORS = [
  { value: 'Equals', label: '=' },
  { value: 'NotEqual', label: '!=' },
  { value: 'Greater', label: '>' },
  { value: 'Less', label: '<' },
  { value: 'GreaterEqual', label: '>=' },
  { value: 'LessEqual', label: '<=' },
  { value: 'BeginsWith', label: 'Begins With' },
  { value: 'Contains', label: 'Contains' },
  { value: 'Like', label: 'Like' },
  { value: 'In', label: 'In List' },
  { value: 'NotIn', label: 'Not In List' },
];

export interface FilterExpression {
  id: string;
  targetType: 'attribute' | 'metric';
  targetId: string;
  targetName: string;
  formId?: string;
  operator: string;
  value: string;
  // For In/NotIn list mode
  selectedElements?: { id: string; name: string }[];
}

interface Props {
  attributes: GridAttribute[];
  metrics: GridMetric[];
  filters: FilterExpression[];
  onFiltersChange: (filters: FilterExpression[]) => void;
  availableElements?: { id: string; name: string }[];
  onLoadElements?: (attributeId: string) => void;
  elementsLoading?: boolean;
}

export function buildViewFilter(filters: FilterExpression[]): any | undefined {
  if (filters.length === 0) return undefined;

  const operands = filters.map(f => {
    // In list / Not in list mode
    if ((f.operator === 'In' || f.operator === 'NotIn') && f.selectedElements && f.selectedElements.length > 0) {
      return {
        operator: f.operator,
        operands: [
          { type: 'attribute', id: f.targetId, name: f.targetName },
          {
            type: 'elements',
            elements: f.selectedElements.map(el => ({ id: el.id, name: el.name })),
          },
        ],
      };
    }

    // Qualification mode
    const left = f.targetType === 'attribute'
      ? { type: 'form', attribute: { id: f.targetId, name: f.targetName }, form: { id: f.formId || '' } }
      : { type: 'metric', id: f.targetId, name: f.targetName };

    return {
      operator: f.operator,
      operands: [
        left,
        { type: 'constant', dataType: f.targetType === 'metric' ? 'Real' : 'Char', value: f.value },
      ],
    };
  });

  if (operands.length === 1) return operands[0];
  return { operator: 'And', operands };
}

export default function FilterBuilder({
  attributes, metrics, filters, onFiltersChange,
  availableElements, onLoadElements, elementsLoading,
}: Props) {
  const [editTarget, setEditTarget] = useState<string>('');
  const [editOp, setEditOp] = useState('Equals');
  const [editValue, setEditValue] = useState('');
  const [editElementSelections, setEditElementSelections] = useState<Set<string>>(new Set());

  const allTargets = [
    ...attributes.map(a => ({ id: a.id, name: a.name, type: 'attribute' as const, formId: a.forms[0]?.id })),
    ...metrics.map(m => ({ id: m.id, name: m.name, type: 'metric' as const })),
  ];

  const currentTarget = allTargets.find(t => t.id === editTarget);
  const isListMode = editOp === 'In' || editOp === 'NotIn';
  const isAttributeTarget = currentTarget?.type === 'attribute';

  function addFilter() {
    if (!currentTarget) return;

    const newFilter: FilterExpression = {
      id: crypto.randomUUID(),
      targetType: currentTarget.type,
      targetId: currentTarget.id,
      targetName: currentTarget.name,
      formId: currentTarget.type === 'attribute' ? currentTarget.formId : undefined,
      operator: editOp,
      value: editValue,
    };

    if (isListMode && editElementSelections.size > 0) {
      newFilter.selectedElements = (availableElements || []).filter(el => editElementSelections.has(el.id));
    }

    onFiltersChange([...filters, newFilter]);
    setEditValue('');
    setEditElementSelections(new Set());
  }

  function removeFilter(id: string) {
    onFiltersChange(filters.filter(f => f.id !== id));
  }

  function handleTargetChange(targetId: string) {
    setEditTarget(targetId);
    setEditElementSelections(new Set());
    const target = allTargets.find(t => t.id === targetId);
    if (target?.type === 'attribute' && (editOp === 'In' || editOp === 'NotIn')) {
      onLoadElements?.(targetId);
    }
  }

  function handleOpChange(op: string) {
    setEditOp(op);
    if ((op === 'In' || op === 'NotIn') && isAttributeTarget && editTarget) {
      onLoadElements?.(editTarget);
    }
  }

  function toggleElement(elId: string) {
    setEditElementSelections(prev => {
      const next = new Set(prev);
      if (next.has(elId)) next.delete(elId);
      else next.add(elId);
      return next;
    });
  }

  return (
    <div>
      {/* Existing filters */}
      {filters.length > 0 && (
        <div className="space-y-1.5 mb-3">
          {filters.map(f => (
            <div key={f.id} className="flex items-center gap-2 bg-[#16213e] px-3 py-1.5 rounded border border-[#1e2d50]">
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${f.targetType === 'attribute' ? 'bg-blue-900/30 text-blue-400' : 'bg-green-900/30 text-green-400'}`}>
                {f.targetType === 'attribute' ? 'ATTR' : 'MTR'}
              </span>
              <span className="text-xs text-gray-200">{f.targetName}</span>
              <span className="text-xs text-yellow-400 font-mono">{f.operator}</span>
              {f.selectedElements && f.selectedElements.length > 0 ? (
                <span className="text-xs text-gray-400 truncate max-w-[200px]">
                  [{f.selectedElements.map(e => e.name).join(', ')}]
                </span>
              ) : (
                <span className="text-xs text-gray-400 font-mono">{f.value}</span>
              )}
              <button onClick={() => removeFilter(f.id)} className="ml-auto text-gray-600 hover:text-red-400">
                <VscTrash className="text-xs" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Add filter form */}
      <div className="space-y-2">
        <div className="flex gap-2">
          <select
            value={editTarget}
            onChange={e => handleTargetChange(e.target.value)}
            className="flex-1 px-2 py-1.5 bg-[#0f0f1a] border border-[#2a2a4a] rounded text-xs text-white focus:outline-none focus:border-red-500"
          >
            <option value="">Select field...</option>
            <optgroup label="Attributes">
              {attributes.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
            </optgroup>
            <optgroup label="Metrics">
              {metrics.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
            </optgroup>
          </select>
          <select
            value={editOp}
            onChange={e => handleOpChange(e.target.value)}
            className="w-32 px-2 py-1.5 bg-[#0f0f1a] border border-[#2a2a4a] rounded text-xs text-white focus:outline-none focus:border-red-500"
          >
            {OPERATORS.filter(op => {
              if (op.value === 'In' || op.value === 'NotIn') return isAttributeTarget;
              return true;
            }).map(op => (
              <option key={op.value} value={op.value}>{op.label}</option>
            ))}
          </select>
        </div>

        {isListMode && isAttributeTarget ? (
          <div className="bg-[#0f0f1a] border border-[#2a2a4a] rounded p-2 max-h-32 overflow-auto">
            {elementsLoading ? (
              <p className="text-xs text-gray-500">Loading elements...</p>
            ) : !availableElements || availableElements.length === 0 ? (
              <p className="text-xs text-gray-500">No elements available</p>
            ) : (
              <div className="space-y-0.5">
                {availableElements.map(el => (
                  <label key={el.id} className="flex items-center gap-2 text-xs cursor-pointer hover:bg-[#1a2a4a] px-1 py-0.5 rounded">
                    <input
                      type="checkbox"
                      checked={editElementSelections.has(el.id)}
                      onChange={() => toggleElement(el.id)}
                      className="accent-blue-500"
                    />
                    <span className="text-gray-300">{el.name}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        ) : (
          <input
            type="text"
            value={editValue}
            onChange={e => setEditValue(e.target.value)}
            placeholder="Value..."
            className="w-full px-2 py-1.5 bg-[#0f0f1a] border border-[#2a2a4a] rounded text-xs text-white focus:outline-none focus:border-red-500"
            onKeyDown={e => e.key === 'Enter' && addFilter()}
          />
        )}

        <button
          onClick={addFilter}
          disabled={!editTarget || (!isListMode && !editValue) || (isListMode && editElementSelections.size === 0)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-[#1e2d50] hover:bg-[#2a3d60] text-gray-300 text-xs rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <VscAdd /> Add Filter
        </button>
      </div>
    </div>
  );
}
