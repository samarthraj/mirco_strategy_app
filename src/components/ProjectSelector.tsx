import { useState, useEffect } from 'react';
import { VscServer, VscCheck } from 'react-icons/vsc';
import { getProjects, setProjectId } from '../api/mstrClient';
import { useApp } from '../context/AppContext';

interface Project {
  id: string;
  name: string;
  description?: string;
  status?: number;
}

export default function ProjectSelector() {
  const { currentProject, setCurrentProject, setCurrentView } = useApp();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    loadProjects();
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
