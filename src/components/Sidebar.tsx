import { VscServer, VscFolder, VscBeaker, VscOutput, VscSignOut, VscDatabase, VscGraph, VscTable, VscDashboard, VscGraphLine, VscGlobe } from 'react-icons/vsc';
import { useApp } from '../context/AppContext';
import type { View } from '../context/AppContext';
import { logout } from '../api/mstrClient';
import navigationConfig from '../config/navigation';

const allNavItems: { view: View; configKey: string; icon: typeof VscFolder; label: string; requiresProject?: boolean }[] = [
  { view: 'projects', configKey: 'projects', icon: VscServer, label: 'Projects' },
  { view: 'dashboard', configKey: 'dashboard', icon: VscDashboard, label: 'Dashboard', requiresProject: true },
  { view: 'browser', configKey: 'object-browser', icon: VscFolder, label: 'Object Browser', requiresProject: true },
  { view: 'data-explorer', configKey: 'data-explorer', icon: VscTable, label: 'Data Explorer', requiresProject: true },
  { view: 'reports', configKey: 'reports', icon: VscGraph, label: 'Reports', requiresProject: true },
  { view: 'rationalization-analysis', configKey: 'rationalization-analysis', icon: VscGraphLine, label: 'Rationalization Analysis', requiresProject: true },
  { view: 'cross-project', configKey: 'cross-project', icon: VscGlobe, label: 'Cross-Project' },
  { view: 'explorer', configKey: 'api-explorer', icon: VscBeaker, label: 'API Explorer' },
  { view: 'logs', configKey: 'request-log', icon: VscOutput, label: 'Request Log' },
];

const navItems = allNavItems.filter(item => navigationConfig[item.configKey] !== false);

export default function Sidebar() {
  const { isAuthenticated, username, currentProject, currentView, setCurrentView, setAuthenticated, setCurrentProject, requestLogs } = useApp();

  if (!isAuthenticated) return null;

  async function handleLogout() {
    try { await logout(); } catch {}
    setAuthenticated(false);
    setCurrentProject(null);
    setCurrentView('login');
  }

  return (
    <div className="w-56 bg-[#16213e] flex flex-col border-r border-[#1e2d50] shrink-0">
      {/* Header */}
      <div className="p-4 border-b border-[#1e2d50]">
        <div className="flex items-center gap-2 mb-2">
          <VscDatabase className="text-red-500 text-lg" />
          <span className="font-bold text-white text-sm tracking-wide">MSTR API Explorer</span>
        </div>
        <div className="text-xs text-gray-400 truncate" title={username}>
          {username}
        </div>
        {currentProject && (
          <div className="text-xs text-blue-400 truncate mt-1" title={currentProject.name}>
            {currentProject.name}
          </div>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 py-2">
        {navItems.map((item) => {
          const disabled = item.requiresProject && !currentProject;
          const active = currentView === item.view;
          return (
            <button
              key={item.view}
              onClick={() => !disabled && setCurrentView(item.view)}
              disabled={disabled}
              className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm text-left transition-colors ${
                active
                  ? 'bg-[#0f3460] text-white border-r-2 border-red-500'
                  : disabled
                  ? 'text-gray-600 cursor-not-allowed'
                  : 'text-gray-400 hover:bg-[#1a2a4a] hover:text-white'
              }`}
            >
              <item.icon className="text-base" />
              {item.label}
              {item.view === 'logs' && requestLogs.length > 0 && (
                <span className="ml-auto bg-[#0f3460] text-xs px-1.5 py-0.5 rounded text-gray-300">
                  {requestLogs.length}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* Logout */}
      <button
        onClick={handleLogout}
        className="flex items-center gap-3 px-4 py-3 text-sm text-gray-400 hover:text-red-400 hover:bg-[#1a1a2e] transition-colors border-t border-[#1e2d50]"
      >
        <VscSignOut className="text-base" />
        Logout
      </button>
    </div>
  );
}
