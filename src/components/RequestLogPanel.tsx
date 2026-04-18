import { useState } from 'react';
import { VscTrash, VscChevronRight, VscChevronDown } from 'react-icons/vsc';
import { useApp } from '../context/AppContext';
import type { RequestLog } from '../api/mstrClient';

function StatusBadge({ status }: { status: number }) {
  const color = status < 300 ? 'bg-green-900/50 text-green-400' : status < 400 ? 'bg-yellow-900/50 text-yellow-400' : 'bg-red-900/50 text-red-400';
  return <span className={`px-1.5 py-0.5 rounded text-xs font-mono ${color}`}>{status}</span>;
}

function MethodBadge({ method }: { method: string }) {
  const colors: Record<string, string> = {
    GET: 'text-green-400',
    POST: 'text-yellow-400',
    PUT: 'text-blue-400',
    DELETE: 'text-red-400',
    PATCH: 'text-purple-400',
  };
  return <span className={`font-mono text-xs font-bold ${colors[method] || 'text-gray-400'}`}>{method}</span>;
}

function LogEntry({ log }: { log: RequestLog }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border-b border-[#1a1a2e]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left flex items-center gap-3 px-4 py-2.5 hover:bg-[#16213e] transition-colors"
      >
        {expanded ? <VscChevronDown className="text-gray-600 shrink-0" /> : <VscChevronRight className="text-gray-600 shrink-0" />}
        <MethodBadge method={log.method} />
        <span className="text-sm text-gray-300 font-mono truncate flex-1">{log.url}</span>
        <StatusBadge status={log.responseStatus} />
        <span className="text-xs text-gray-600 shrink-0">{log.duration}ms</span>
        <span className="text-xs text-gray-700 shrink-0">
          {log.timestamp.toLocaleTimeString()}
        </span>
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-3">
          {/* Request Headers */}
          <div>
            <h4 className="text-xs text-gray-500 uppercase tracking-wider mb-1">Request Headers</h4>
            <pre className="text-xs text-gray-400 bg-[#0a0a15] rounded p-3 overflow-auto max-h-40 font-mono">
              {JSON.stringify(log.requestHeaders, null, 2)}
            </pre>
          </div>

          {/* Request Body */}
          {log.requestBody != null && (
            <div>
              <h4 className="text-xs text-gray-500 uppercase tracking-wider mb-1">Request Body</h4>
              <pre className="text-xs text-gray-400 bg-[#0a0a15] rounded p-3 overflow-auto max-h-40 font-mono">
                {JSON.stringify(log.requestBody, null, 2)}
              </pre>
            </div>
          )}

          {/* Response */}
          <div>
            <h4 className="text-xs text-gray-500 uppercase tracking-wider mb-1">Response Body</h4>
            <pre className="text-xs text-gray-400 bg-[#0a0a15] rounded p-3 overflow-auto max-h-60 font-mono whitespace-pre-wrap">
              {String(typeof log.responseBody === 'string' ? log.responseBody : JSON.stringify(log.responseBody, null, 2))}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

export default function RequestLogPanel() {
  const { requestLogs, clearLogs } = useApp();

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="p-4 border-b border-[#1e2d50] bg-[#16213e] flex items-center justify-between">
        <div>
          <h2 className="text-white font-bold text-sm">Request Log</h2>
          <p className="text-xs text-gray-500">{requestLogs.length} requests captured</p>
        </div>
        <button
          onClick={clearLogs}
          disabled={requestLogs.length === 0}
          className="px-3 py-1.5 text-xs text-gray-400 hover:text-red-400 bg-[#0f0f1a] rounded transition-colors disabled:opacity-30 flex items-center gap-1.5"
        >
          <VscTrash /> Clear
        </button>
      </div>

      <div className="flex-1 overflow-auto">
        {requestLogs.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            <p>No requests captured yet</p>
            <p className="text-xs mt-1">All API calls will be logged here</p>
          </div>
        ) : (
          requestLogs.map((log) => <LogEntry key={log.id} log={log} />)
        )}
      </div>
    </div>
  );
}
