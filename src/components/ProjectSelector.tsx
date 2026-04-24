import { useState, useEffect } from 'react';
import { VscServer, VscCheck, VscLayers } from 'react-icons/vsc';
import { getProjects, setProjectId } from '../api/mstrClient';
import { fetchCombinedStatus } from '../api/rationalizationClient';
import type { CombinedStatus } from '../api/rationalizationClient';
import { useApp } from '../context/AppContext';

interface Project {
  id: string;
  name: string;
  description?: string;
  status?: number;
}

// Synthetic virtual project — not an MSTR project. Selecting it routes
// the user straight to Rationalization Analysis against the combined
// final-kept set from GO/GI/INSIGHT.
const COMBINED_PROJECT = {
  id: 'combined-reports',
  name: 'Combined Reports Project',
  description: 'Virtual — final-kept reports from GO + GI + INSIGHT combined for cross-project rationalization',
};

export default function ProjectSelector() {
  const { currentProject, setCurrentProject, setCurrentView } = useApp();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [combinedStatus, setCombinedStatus] = useState<CombinedStatus | null>(null);

  useEffect(() => {
    loadProjects();
    fetchCombinedStatus().then((s) => setCombinedStatus(s));
  }, []);

  async function loadProjects() {
    setLoading(true);
    setError('');
    try {
      const data = await getProjects();
      setProjects(Array.isArray(data) ? data : []);
    } catch (err: any) {
      setError(err.response?.data?.message || 'Failed to load projects');
    } finally {
      setLoading(false);
    }
  }

  function handleSelect(project: Project) {
    setProjectId(project.id);
    setCurrentProject({ id: project.id, name: project.name });
    setCurrentView('browser');
  }

  function handleSelectCombined() {
    // The combined project is local-only; skip setProjectId (no MSTR
    // project to switch to) and route directly to Rationalization.
    setCurrentProject({ id: COMBINED_PROJECT.id, name: COMBINED_PROJECT.name });
    setCurrentView('rationalization-analysis');
  }

  return (
    <div className="flex-1 p-6 overflow-auto">
      <div className="max-w-3xl mx-auto">
        <h2 className="text-xl font-bold text-white mb-1">Select a Project</h2>
        <p className="text-gray-400 text-sm mb-6">Choose a project to browse its objects and data</p>

        {loading && (
          <div className="text-center py-12 text-gray-400">
            <div className="animate-spin inline-block w-6 h-6 border-2 border-gray-600 border-t-red-500 rounded-full mb-3" />
            <p>Loading projects...</p>
          </div>
        )}

        {error && (
          <div className="p-4 bg-red-900/30 border border-red-800 rounded-lg text-red-300 text-sm mb-4">
            {error}
            <button onClick={loadProjects} className="ml-3 underline hover:text-red-200">Retry</button>
          </div>
        )}

        {/* Virtual project — combined reports across GO/GI/INSIGHT */}
        <div className="mb-4">
          <div className="text-[10px] uppercase tracking-wider text-violet-300 font-bold mb-1.5">
            Rationalization workspace
          </div>
          <button
            onClick={handleSelectCombined}
            className={`w-full text-left p-4 rounded-xl border transition-all ${
              currentProject?.id === COMBINED_PROJECT.id
                ? 'bg-violet-900/40 border-violet-500 text-white ring-1 ring-violet-500/40'
                : 'bg-gradient-to-r from-violet-950/40 to-[#16213e] border-violet-800/40 text-gray-200 hover:border-violet-600 hover:bg-violet-900/30'
            }`}
          >
            <div className="flex items-center gap-3">
              <VscLayers className="text-lg text-violet-300 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="font-medium flex items-center gap-2">
                  {COMBINED_PROJECT.name}
                  {combinedStatus?.stale && (
                    <span className="text-[9px] font-semibold text-amber-300 bg-amber-950/40 border border-amber-700/50 rounded px-1.5 py-0.5">
                      {combinedStatus.lastBuiltAt ? 'STALE' : 'NOT BUILT'}
                    </span>
                  )}
                </div>
                <div className="text-xs text-gray-400 mt-0.5">{COMBINED_PROJECT.description}</div>
                <div className="text-[11px] text-gray-500 mt-1 flex items-center gap-2 flex-wrap">
                  {combinedStatus && combinedStatus.totalReports > 0 ? (
                    <>
                      <span>{combinedStatus.totalReports.toLocaleString()} reports</span>
                      {combinedStatus.lastBuiltAt && (
                        <>
                          <span>·</span>
                          <span>built {new Date(combinedStatus.lastBuiltAt).toLocaleString()}</span>
                        </>
                      )}
                    </>
                  ) : (
                    <span className="italic">Not built yet — click to open and press "Rebuild"</span>
                  )}
                </div>
              </div>
              {currentProject?.id === COMBINED_PROJECT.id && <VscCheck className="text-green-400 text-lg" />}
            </div>
          </button>
        </div>

        <div className="text-[10px] uppercase tracking-wider text-gray-500 font-bold mb-1.5">
          MSTR projects
        </div>
        <div className="grid gap-3">
          {projects.map((p) => (
            <button
              key={p.id}
              onClick={() => handleSelect(p)}
              className={`w-full text-left p-4 rounded-xl border transition-all ${
                currentProject?.id === p.id
                  ? 'bg-[#0f3460] border-red-500/50 text-white'
                  : 'bg-[#16213e] border-[#1e2d50] text-gray-300 hover:border-[#3a4a6a] hover:bg-[#1a2a4a]'
              }`}
            >
              <div className="flex items-center gap-3">
                <VscServer className="text-lg text-red-400 shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="font-medium">{p.name}</div>
                  {p.description && <div className="text-xs text-gray-500 mt-0.5 truncate">{p.description}</div>}
                  <div className="text-xs text-gray-600 mt-1 font-mono">{p.id}</div>
                </div>
                {currentProject?.id === p.id && <VscCheck className="text-green-400 text-lg" />}
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
