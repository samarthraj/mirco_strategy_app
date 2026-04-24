import { AppProvider, useApp } from './context/AppContext';
import Sidebar from './components/Sidebar';
import LoginPanel from './components/LoginPanel';
import ProjectSelector from './components/ProjectSelector';
import ObjectBrowser from './components/ObjectBrowser';
import DataViewer from './components/DataViewer';
import ApiExplorer from './components/ApiExplorer';
import RequestLogPanel from './components/RequestLogPanel';
import ReportSearch from './components/ReportSearch';
import DataExplorer from './components/DataExplorer';
import Dashboard from './components/Dashboard';
import RationalizationAnalysis from './components/RationalizationAnalysis';
import CrossProject from './components/CrossProject';
import { VscSignOut } from 'react-icons/vsc';
import { logout as mstrLogout } from './api/mstrClient';

function MainContent() {
  const { currentView, isAuthenticated } = useApp();

  if (!isAuthenticated) return <LoginPanel />;

  switch (currentView) {
    case 'projects': return <ProjectSelector />;
    case 'dashboard': return <Dashboard />;
    case 'browser': return <ObjectBrowser />;
    case 'data-explorer': return <DataExplorer />;
    case 'reports': return <ReportSearch />;
    case 'rationalization-analysis': return <RationalizationAnalysis />;
    case 'cross-project': return <CrossProject />;
    case 'report':
    case 'cube': return <DataViewer />;
    case 'explorer': return <ApiExplorer />;
    case 'logs': return <RequestLogPanel />;
    default: return <ProjectSelector />;
  }
}

function TopBar() {
  const {
    isAuthenticated, currentProject, username,
    setAuthenticated, setCurrentProject, setCurrentView,
  } = useApp();
  if (!isAuthenticated) return null;

  async function handleLogout() {
    // Fire-and-forget the server logout — UI should return to login
    // immediately even if the session was already invalidated server-side.
    try { await mstrLogout(); } catch { /* best-effort */ }
    setAuthenticated(false);
    setCurrentProject(null);
    setCurrentView('login');
  }

  return (
    <div className="h-10 bg-[#16213e] border-b border-[#1e2d50] flex items-center justify-between px-4 shrink-0">
      <div className="text-sm text-gray-300">
        {currentProject && (
          <>
            <span className="text-gray-500">Project Name: </span>
            <span className="text-blue-400 font-medium">{currentProject.name}</span>
          </>
        )}
      </div>
      <div className="flex items-center gap-3">
        {username && (
          <span className="text-xs text-gray-400">
            Signed in as <span className="text-gray-200 font-medium">{username}</span>
          </span>
        )}
        <button
          type="button"
          onClick={handleLogout}
          className="flex items-center gap-1 text-xs text-gray-300 hover:text-white bg-[#0f0f1a] border border-[#1e2d50] hover:border-red-500/50 rounded px-2.5 py-1 transition-colors"
          title="Log out of MicroStrategy and return to the login screen"
        >
          <VscSignOut />
          Logout
        </button>
        <img src="/bourntec-logo.svg" alt="Bourntec" className="h-5" />
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <div className="h-screen flex flex-col overflow-hidden bg-[#0f0f1a]">
        <TopBar />
        <div className="flex-1 flex overflow-hidden">
          <Sidebar />
          <MainContent />
        </div>
      </div>
    </AppProvider>
  );
}
