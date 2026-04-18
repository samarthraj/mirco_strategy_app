import { useState, useEffect } from 'react';
import { VscSearch, VscPlay } from 'react-icons/vsc';
import { searchObjects } from '../api/mstrClient';
import { useApp } from '../context/AppContext';

interface ReportItem {
  id: string;
  name: string;
  type: number;
  subtype?: number;
  dateModified?: string;
  owner?: { name: string };
  certifiedInfo?: { certified: boolean };
  description?: string;
}

export default function ReportSearch() {
  const { selectObject } = useApp();
  const [query, setQuery] = useState('');
  const [reports, setReports] = useState<ReportItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [searched, setSearched] = useState(false);

  useEffect(() => {
    handleSearch();
  }, []);

  async function handleSearch(e?: React.FormEvent) {
    e?.preventDefault();
    setLoading(true);
    setError('');
    setSearched(true);
    try {
      const data = await searchObjects({
        name: query || undefined,
        type: 3, // Report object type
        limit: 100,
        getAncestors: false,
      });
      setReports(data.result || []);
    } catch (err: any) {
      setError(err.response?.data?.message || 'Failed to search reports');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex-1 p-6 overflow-auto">
      <div className="max-w-5xl mx-auto">
        <h2 className="text-xl font-bold text-white mb-1">Reports</h2>
        <p className="text-gray-400 text-sm mb-6">Search and browse reports in the current project</p>

        <form onSubmit={handleSearch} className="flex gap-3 mb-6">
          <div className="relative flex-1">
            <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search reports by name..."
              className="w-full pl-9 pr-3 py-2.5 bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg text-white text-sm focus:outline-none focus:border-red-500 transition-colors"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="px-5 py-2.5 bg-gradient-to-r from-red-600 to-red-700 hover:from-red-700 hover:to-red-800 text-white text-sm font-medium rounded-lg transition-all disabled:opacity-50"
          >
            {loading ? 'Searching...' : 'Search'}
          </button>
        </form>

        {error && (
          <div className="p-4 bg-red-900/30 border border-red-800 rounded-lg text-red-300 text-sm mb-4">
            {error}
          </div>
        )}

        {loading && (
          <div className="text-center py-12 text-gray-400">
            <div className="animate-spin inline-block w-6 h-6 border-2 border-gray-600 border-t-red-500 rounded-full mb-3" />
            <p>Searching reports...</p>
          </div>
        )}

        {!loading && searched && reports.length === 0 && !error && (
          <div className="text-center py-12 text-gray-500">No reports found.</div>
        )}

        {!loading && reports.length > 0 && (
          <div className="border border-[#1e2d50] rounded-xl overflow-hidden">
            <table className="w-full text-sm text-left">
              <thead>
                <tr className="bg-[#1a2a4a] text-gray-400 text-xs uppercase tracking-wider">
                  <th className="px-4 py-3 font-medium">Name</th>
                  <th className="px-4 py-3 font-medium">Owner</th>
                  <th className="px-4 py-3 font-medium">Modified</th>
                  <th className="px-4 py-3 font-medium">Certified</th>
                  <th className="px-4 py-3 font-medium w-20">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1e2d50]">
                {reports.map((r) => (
                  <tr key={r.id} className="bg-[#16213e] hover:bg-[#1a2a4a] transition-colors">
                    <td className="px-4 py-3">
                      <div className="text-white font-medium">{r.name}</div>
                      <div className="text-xs text-gray-500 font-mono mt-0.5">{r.id}</div>
                    </td>
                    <td className="px-4 py-3 text-gray-400">{r.owner?.name || '-'}</td>
                    <td className="px-4 py-3 text-gray-400">
                      {r.dateModified ? new Date(r.dateModified).toLocaleDateString() : '-'}
                    </td>
                    <td className="px-4 py-3">
                      {r.certifiedInfo?.certified ? (
                        <span className="text-green-400 text-xs font-medium px-2 py-0.5 bg-green-900/30 rounded-full">Yes</span>
                      ) : (
                        <span className="text-gray-500">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => selectObject(r.id, 3)}
                        className="flex items-center gap-1.5 text-xs text-red-400 hover:text-red-300 transition-colors"
                        title="Execute report"
                      >
                        <VscPlay /> Run
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {!loading && reports.length > 0 && (
          <div className="text-xs text-gray-500 mt-3">{reports.length} report{reports.length !== 1 ? 's' : ''} found</div>
        )}
      </div>
    </div>
  );
}
