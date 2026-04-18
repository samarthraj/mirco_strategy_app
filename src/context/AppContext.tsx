import { createContext, useContext, useState, useCallback, useEffect } from 'react';
import type { ReactNode } from 'react';
import type { RequestLog } from '../api/mstrClient';
import { onRequestLog, getBaseUrl } from '../api/mstrClient';

export type View = 'login' | 'projects' | 'dashboard' | 'browser' | 'report' | 'cube' | 'explorer' | 'logs' | 'reports' | 'inventory' | 'data-explorer' | 'rationalization' | 'rationalization-analysis' | 'object-tree';

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

export function AppProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setAuthenticated] = useState(false);
  const [username, setUsername] = useState('');
  const [baseUrl, setBaseUrl] = useState(getBaseUrl());
  const [currentProject, setCurrentProject] = useState<{ id: string; name: string } | null>(null);
  const [currentView, setCurrentView] = useState<View>('login');
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
