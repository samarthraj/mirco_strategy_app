import { createContext, useContext, useState, useCallback, useEffect } from 'react';
import type { ReactNode } from 'react';
import type { RequestLog } from '../api/mstrClient';
import { onRequestLog, getBaseUrl } from '../api/mstrClient';

export type View = 'login' | 'projects' | 'dashboard' | 'browser' | 'report' | 'cube' | 'explorer' | 'logs' | 'reports' | 'data-explorer' | 'rationalization-analysis' | 'cross-project';

interface AppState {
  isAuthenticated: boolean;
  setAuthenticated: (v: boolean) => void;
  username: string;
  setUsername: (v: string) => void;
  baseUrl: string;
  setBaseUrl: (v: string) => void;
  currentProject: { id: string; name: string } | null;
  setCurrentProject: (p: { id: string; name: string } | null) => void;
  currentView: View;
  setCurrentView: (v: View) => void;
  requestLogs: RequestLog[];
  clearLogs: () => void;
  selectedObjectId: string | null;
  selectedObjectType: number | null;
  selectObject: (id: string, type: number) => void;
}

const AppContext = createContext<AppState | null>(null);

// Storage-backed hook. Uses sessionStorage for auth so a fresh browser launch
// still shows the login screen, but HMR reloads inside the same tab keep the
// session alive. Non-auth preferences (selected project, last view) use
// localStorage so they survive across browser restarts.
function useStoredState<T>(
  key: string,
  initial: T,
  storage: 'local' | 'session' = 'local',
): [T, (v: T | ((prev: T) => T)) => void] {
  const store = storage === 'session' ? window.sessionStorage : window.localStorage;
  const [v, setV] = useState<T>(() => {
    try {
      const raw = store.getItem(key);
      if (raw != null) return JSON.parse(raw) as T;
    } catch { /* ignore */ }
    return initial;
  });
  useEffect(() => {
    try { store.setItem(key, JSON.stringify(v)); } catch { /* ignore */ }
  }, [key, v, store]);
  return [v, setV];
}

// One-time cleanup: earlier versions wrote auth state to localStorage, which
// meant a fresh browser launch skipped the login screen. Purge those keys so
// session-scoped storage becomes the single source of truth.
try {
  ['app.isAuthenticated', 'app.username', 'app.currentProject', 'app.currentView']
    .forEach((k) => window.localStorage.removeItem(k));
} catch { /* ignore */ }

export function AppProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setAuthenticated] = useStoredState<boolean>('app.isAuthenticated', false, 'session');
  const [username, setUsername] = useStoredState<string>('app.username', '', 'session');
  const [baseUrl, setBaseUrl] = useState(getBaseUrl());
  const [currentProject, setCurrentProject] = useStoredState<{ id: string; name: string } | null>('app.currentProject', null, 'session');
  const [currentView, setCurrentView] = useStoredState<View>('app.currentView', 'login', 'session');
  const [requestLogs, setRequestLogs] = useState<RequestLog[]>([]);
  const [selectedObjectId, setSelectedObjectId] = useState<string | null>(null);
  const [selectedObjectType, setSelectedObjectType] = useState<number | null>(null);

  const clearLogs = useCallback(() => setRequestLogs([]), []);

  const selectObject = useCallback((id: string, type: number) => {
    setSelectedObjectId(id);
    setSelectedObjectType(type);
    // type 3 = report, type 21 = cube
    if (type === 3) setCurrentView('report');
    else if (type === 21) setCurrentView('cube');
  }, []);

  useEffect(() => {
    return onRequestLog((log) => {
      setRequestLogs((prev) => [log, ...prev].slice(0, 200));
    });
  }, []);

  return (
    <AppContext.Provider
      value={{
        isAuthenticated, setAuthenticated,
        username, setUsername,
        baseUrl, setBaseUrl,
        currentProject, setCurrentProject,
        currentView, setCurrentView,
        requestLogs, clearLogs,
        selectedObjectId, selectedObjectType, selectObject,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be inside AppProvider');
  return ctx;
}
