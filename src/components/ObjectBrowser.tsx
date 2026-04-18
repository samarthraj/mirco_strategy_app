import { useState, useEffect } from 'react';
import {
  VscFolder, VscFile, VscSearch,
  VscChevronRight, VscGraph, VscTable,
  VscSymbolMisc, VscHome
} from 'react-icons/vsc';
import { searchObjects, getFolderContents } from '../api/mstrClient';
import { useApp } from '../context/AppContext';

// MicroStrategy object type mapping
const TYPE_LABELS: Record<number, string> = {
  3: 'Report', 8: 'Folder', 10: 'Prompt', 12: 'Attribute',
  13: 'Function', 14: 'Search', 21: 'Cube', 47: 'Consolidation',
  55: 'Dossier', 768: 'Dashboard',
};

const TYPE_ICONS: Record<number, typeof VscFile> = {
  3: VscGraph, 8: VscFolder, 21: VscTable, 55: VscSymbolMisc,
};

interface MstrObject {
  id: string;
  name: string;
  type: number;
  subtype?: number;
  dateModified?: string;
  description?: string;
}

export default function ObjectBrowser() {
  const { selectObject } = useApp();
  const [objects, setObjects] = useState<MstrObject[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchType, setSearchType] = useState<number | undefined>(undefined);
  const [breadcrumbs, setBreadcrumbs] = useState<{ id: string; name: string }[]>([]);
  const [currentFolderId, setCurrentFolderId] = useState<string | undefined>(undefined);

  useEffect(() => {
    loadRootFolders();
  }, []);

  async function loadRootFolders() {
    setLoading(true);
    setError('');
    setBreadcrumbs([]);
    setCurrentFolderId(undefined);
    try {
      const data = await searchObjects({ type: 8, pattern: 4, limit: 50 });
      setObjects(data.result || []);
    } catch (err: any) {
      setError(err.response?.data?.message || 'Failed to load folders');
    } finally {
      setLoading(false);
    }
  }

  async function openFolder(folder: MstrObject) {
    setLoading(true);
    setError('');
    setCurrentFolderId(folder.id);
    setBreadcrumbs((prev) => [...prev, { id: folder.id, name: folder.name }]);
    try {
      const data = await getFolderContents(folder.id);
      setObjects(Array.isArray(data) ? data : data.result || data.children || []);
    } catch (err: any) {
      setError(err.response?.data?.message || 'Failed to load folder');
    } finally {
      setLoading(false);
    }
  }

  async function navigateToBreadcrumb(index: number) {
    if (index < 0) {
      loadRootFolders();
      return;
    }
    const crumb = breadcrumbs[index];
    setBreadcrumbs((prev) => prev.slice(0, index + 1));
    setCurrentFolderId(crumb.id);
    setLoading(true);
    setError('');
    try {
      const data = await getFolderContents(crumb.id);
      setObjects(Array.isArray(data) ? data : data.result || data.children || []);
    } catch (err: any) {
      setError(err.response?.data?.message || 'Failed to load folder');
    } finally {
      setLoading(false);
    }
  }

  async function handleSearch() {
    if (!searchQuery.trim()) return;
    setLoading(true);
    setError('');
    setBreadcrumbs([]);
    try {
      const params: any = { name: searchQuery, pattern: 4, limit: 50 };
      if (searchType) params.type = searchType;
      if (currentFolderId) params.root = currentFolderId;
      const data = await searchObjects(params);
      setObjects(data.result || []);
    } catch (err: any) {
      setError(err.response?.data?.message || 'Search failed');
    } finally {
      setLoading(false);
    }
  }

  function handleObjectClick(obj: MstrObject) {
    if (obj.type === 8) {
      openFolder(obj);
    } else if (obj.type === 3 || obj.type === 21) {
      selectObject(obj.id, obj.type);
    }
  }

  const Icon = (type: number) => TYPE_ICONS[type] || VscFile;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Search bar */}
      <div className="p-4 border-b border-[#1e2d50] bg-[#16213e]">
        <div className="flex gap-2 mb-3">
          <div className="flex-1 relative">
            <VscSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              placeholder="Search objects..."
              className="w-full pl-9 pr-3 py-2 bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg text-white text-sm focus:outline-none focus:border-red-500"
            />
          </div>
          <select
            value={searchType ?? ''}
            onChange={(e) => setSearchType(e.target.value ? Number(e.target.value) : undefined)}
            className="px-3 py-2 bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg text-white text-sm focus:outline-none focus:border-red-500"
          >
            <option value="">All Types</option>
            <option value="3">Reports</option>
            <option value="21">Cubes</option>
            <option value="55">Dossiers</option>
            <option value="8">Folders</option>
            <option value="12">Attributes</option>
          </select>
          <button onClick={handleSearch} className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-sm transition-colors">
            Search
          </button>
        </div>

        {/* Breadcrumbs */}
        <div className="flex items-center gap-1 text-sm text-gray-400 overflow-x-auto">
          <button onClick={() => navigateToBreadcrumb(-1)} className="hover:text-white transition-colors flex items-center gap-1 shrink-0">
            <VscHome className="text-xs" /> Root
          </button>
          {breadcrumbs.map((crumb, i) => (
            <span key={crumb.id} className="flex items-center gap-1 shrink-0">
              <VscChevronRight className="text-xs text-gray-600" />
              <button onClick={() => navigateToBreadcrumb(i)} className="hover:text-white transition-colors truncate max-w-[150px]">
                {crumb.name}
              </button>
            </span>
          ))}
        </div>
      </div>

      {/* Object list */}
      <div className="flex-1 overflow-auto p-4">
        {loading && (
          <div className="text-center py-12 text-gray-400">
            <div className="animate-spin inline-block w-6 h-6 border-2 border-gray-600 border-t-red-500 rounded-full mb-3" />
            <p>Loading...</p>
          </div>
        )}

        {error && (
          <div className="p-4 bg-red-900/30 border border-red-800 rounded-lg text-red-300 text-sm mb-4">
            {error}
          </div>
        )}

        {!loading && !error && objects.length === 0 && (
          <div className="text-center py-12 text-gray-500">
            <VscFolder className="text-4xl mx-auto mb-3 opacity-50" />
            <p>No objects found</p>
          </div>
        )}

        <div className="grid gap-1">
          {objects.map((obj) => {
            const ObjIcon = Icon(obj.type);
            const isClickable = obj.type === 8 || obj.type === 3 || obj.type === 21;
            return (
              <button
                key={obj.id}
                onClick={() => handleObjectClick(obj)}
                className={`w-full text-left flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors ${
                  isClickable
                    ? 'hover:bg-[#16213e] cursor-pointer'
                    : 'cursor-default opacity-70'
                }`}
              >
                <ObjIcon className={`text-base shrink-0 ${obj.type === 8 ? 'text-yellow-500' : obj.type === 3 ? 'text-blue-400' : obj.type === 21 ? 'text-green-400' : 'text-gray-500'}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-white truncate">{obj.name}</div>
                  <div className="text-xs text-gray-500">{TYPE_LABELS[obj.type] || `Type ${obj.type}`}</div>
                </div>
                <div className="text-xs text-gray-600 font-mono shrink-0">{obj.id.slice(0, 8)}...</div>
                {obj.type === 8 && <VscChevronRight className="text-gray-600 shrink-0" />}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
