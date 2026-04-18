import { useState } from 'react';
import { VscPlay, VscChevronDown, VscChevronRight } from 'react-icons/vsc';
import { sendCustomRequest, getAuthToken, getProjectId } from '../api/mstrClient';

const PRESETS = [
  { label: 'Session Info', method: 'GET', path: '/sessions' },
  { label: 'List Projects', method: 'GET', path: '/projects' },
  { label: 'Server Status', method: 'GET', path: '/status' },
  { label: 'Predefined Folders', method: 'GET', path: '/folders/preDefined' },
  { label: 'Search Reports', method: 'GET', path: '/searches/results?type=3&pattern=4&limit=10' },
  { label: 'Search Cubes', method: 'GET', path: '/searches/results?type=21&pattern=4&limit=10' },
  { label: 'Search Dossiers', method: 'GET', path: '/searches/results?type=55&pattern=4&limit=10' },
  { label: 'User Info', method: 'GET', path: '/users/info' },
  { label: 'User Library', method: 'GET', path: '/library' },
];

export default function ApiExplorer() {
  const [method, setMethod] = useState('GET');
  const [path, setPath] = useState('/sessions');
  const [body, setBody] = useState('');
  const [customHeaders, setCustomHeaders] = useState('');
  const [response, setResponse] = useState<any>(null);
  const [responseStatus, setResponseStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showPresets, setShowPresets] = useState(true);
  const [showHeaders, setShowHeaders] = useState(false);

  async function handleSend() {
    setLoading(true);
    setError('');
    setResponse(null);
    setResponseStatus(null);
    try {
      let parsedBody: unknown;
      if (body.trim()) {
        parsedBody = JSON.parse(body);
      }
      let parsedHeaders: Record<string, string> = {};
      if (customHeaders.trim()) {
        parsedHeaders = JSON.parse(customHeaders);
      }
      const res = await sendCustomRequest(method, path, parsedBody, parsedHeaders);
      setResponse(res.data);
      setResponseStatus(res.status);
    } catch (err: any) {
      if (err.response) {
        setResponse(err.response.data);
        setResponseStatus(err.response.status);
      } else {
        setError(err.message || 'Request failed');
      }
    } finally {
      setLoading(false);
    }
  }

  function applyPreset(preset: typeof PRESETS[0]) {
    setMethod(preset.method);
    setPath(preset.path);
    setBody('');
  }

  const statusColor = responseStatus
    ? responseStatus < 300 ? 'text-green-400' : responseStatus < 400 ? 'text-yellow-400' : 'text-red-400'
    : 'text-gray-400';

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Top: request builder */}
      <div className="p-4 border-b border-[#1e2d50] bg-[#16213e]">
        <h2 className="text-white font-bold text-sm mb-3">API Explorer</h2>

        {/* Method + Path */}
        <div className="flex gap-2 mb-3">
          <select
            value={method}
            onChange={(e) => setMethod(e.target.value)}
            className="px-3 py-2 bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg text-white text-sm font-mono focus:outline-none focus:border-red-500 w-24"
          >
            {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
          <input
            type="text"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/api/endpoint"
            className="flex-1 px-3 py-2 bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg text-white text-sm font-mono focus:outline-none focus:border-red-500"
          />
          <button
            onClick={handleSend}
            disabled={loading}
            className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-sm transition-colors disabled:opacity-50 flex items-center gap-1.5"
          >
            <VscPlay /> {loading ? 'Sending...' : 'Send'}
          </button>
        </div>

        {/* Auth info */}
        <div className="flex gap-4 text-xs text-gray-500 mb-3">
          <span>Token: {getAuthToken() ? '****' + getAuthToken()!.slice(-6) : 'none'}</span>
          <span>Project: {getProjectId() || 'none'}</span>
        </div>

        {/* Custom headers toggle */}
        <button onClick={() => setShowHeaders(!showHeaders)} className="text-xs text-gray-400 hover:text-white flex items-center gap-1 mb-2">
          {showHeaders ? <VscChevronDown /> : <VscChevronRight />} Custom Headers
        </button>
        {showHeaders && (
          <textarea
            value={customHeaders}
            onChange={(e) => setCustomHeaders(e.target.value)}
            placeholder='{"X-Custom-Header": "value"}'
            rows={2}
            className="w-full px-3 py-2 bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg text-white text-sm font-mono focus:outline-none focus:border-red-500 mb-3 resize-none"
          />
        )}

        {/* Body (for POST/PUT/PATCH) */}
        {['POST', 'PUT', 'PATCH'].includes(method) && (
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Request body (JSON)"
            rows={4}
            className="w-full px-3 py-2 bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg text-white text-sm font-mono focus:outline-none focus:border-red-500 resize-none"
          />
        )}
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* Presets sidebar */}
        <div className="w-48 border-r border-[#1e2d50] bg-[#0f0f1a] overflow-auto shrink-0">
          <div className="p-3">
            <button onClick={() => setShowPresets(!showPresets)} className="text-xs text-gray-400 hover:text-white flex items-center gap-1 mb-2 font-medium uppercase tracking-wider">
              {showPresets ? <VscChevronDown /> : <VscChevronRight />} Quick Actions
            </button>
            {showPresets && (
              <div className="grid gap-1">
                {PRESETS.map((p) => (
                  <button
                    key={p.label}
                    onClick={() => applyPreset(p)}
                    className="text-left px-2 py-1.5 text-xs text-gray-400 hover:text-white hover:bg-[#16213e] rounded transition-colors"
                  >
                    <span className={`font-mono mr-1.5 ${p.method === 'GET' ? 'text-green-500' : 'text-yellow-500'}`}>{p.method}</span>
                    {p.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Response */}
        <div className="flex-1 overflow-auto p-4">
          {error && (
            <div className="p-4 bg-red-900/30 border border-red-800 rounded-lg text-red-300 text-sm mb-4">
              {error}
            </div>
          )}

          {responseStatus !== null && (
            <div className="mb-3 flex items-center gap-2">
              <span className={`text-sm font-mono font-bold ${statusColor}`}>
                {responseStatus}
              </span>
              <span className="text-xs text-gray-500">
                {responseStatus < 300 ? 'OK' : responseStatus < 400 ? 'Redirect' : 'Error'}
              </span>
            </div>
          )}

          {response !== null && (
            <pre className="text-xs text-gray-300 bg-[#0a0a15] rounded-lg p-4 overflow-auto whitespace-pre-wrap font-mono border border-[#1e2d50]">
              {typeof response === 'string' ? response : JSON.stringify(response, null, 2)}
            </pre>
          )}

          {response === null && !error && !loading && (
            <div className="text-center py-12 text-gray-500">
              <p>Send a request to see the response</p>
            </div>
          )}

          {loading && (
            <div className="text-center py-12 text-gray-400">
              <div className="animate-spin inline-block w-6 h-6 border-2 border-gray-600 border-t-red-500 rounded-full mb-3" />
              <p>Sending request...</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
