import { useState } from 'react';
import { VscKey } from 'react-icons/vsc';
import { login, setBaseUrl as setClientBaseUrl } from '../api/mstrClient';
import { useApp } from '../context/AppContext';

const ENVIRONMENTS = [
  { label: 'MicroStrategy Demo', url: 'https://demo.microstrategy.com/MicroStrategyLibrary' },
  { label: 'Ralph Lauren SBX', url: 'https://rlanalytics-sbx.ralphlauren.com/MicroStrategyLibrary' },
] as const;

export default function LoginPanel() {
  const { setAuthenticated, setUsername: setAppUsername, setCurrentView, baseUrl, setBaseUrl } = useApp();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loginMode, setLoginMode] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  function handleEnvironmentChange(url: string) {
    setBaseUrl(url);
    setUsername('');
    setPassword('');
  }

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      await setClientBaseUrl(baseUrl);
      await login(username, password, loginMode);
      setAuthenticated(true);
      setAppUsername(username);
      setCurrentView('projects');
    } catch (err: any) {
      setError(err.response?.data?.message || err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex-1 flex items-center justify-center bg-[#0f0f1a]">
      <div className="w-full max-w-md">
        {/* Logo area */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center mb-4">
            <img src="/bourntec-logo.svg" alt="Bourntec" className="h-10" />
          </div>
          <h1 className="text-2xl font-bold text-white">MicroStrategy API Explorer</h1>
          <p className="text-gray-400 text-sm mt-2">
            Connect to your MicroStrategy environment
          </p>
        </div>

        {/* Form */}
        <form onSubmit={handleLogin} className="bg-[#16213e] rounded-xl p-6 shadow-2xl border border-[#1e2d50]">
          <div className="mb-4">
            <label className="block text-xs text-gray-400 mb-1.5 uppercase tracking-wider">Environment</label>
            <select
              value={ENVIRONMENTS.find((e) => e.url === baseUrl) ? baseUrl : '__custom'}
              onChange={(e) => {
                if (e.target.value === '__custom') {
                  setBaseUrl('');
                  setUsername('');
                  setPassword('');
                } else {
                  handleEnvironmentChange(e.target.value);
                }
              }}
              className="w-full px-3 py-2.5 bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg text-white text-sm focus:outline-none focus:border-red-500 mb-2"
            >
              {ENVIRONMENTS.map((env) => (
                <option key={env.url} value={env.url}>{env.label}</option>
              ))}
              <option value="__custom">Custom URL...</option>
            </select>
            {!ENVIRONMENTS.find((e) => e.url === baseUrl) && (
              <input
                type="text"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://your-server.com/MicroStrategyLibrary"
                className="w-full px-3 py-2.5 bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg text-white text-sm focus:outline-none focus:border-red-500 transition-colors"
              />
            )}
          </div>

          <div className="mb-4">
            <label className="block text-xs text-gray-400 mb-1.5 uppercase tracking-wider">Username</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Enter username"
              className="w-full px-3 py-2.5 bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg text-white text-sm focus:outline-none focus:border-red-500 transition-colors"
              autoFocus
            />
          </div>

          <div className="mb-4">
            <label className="block text-xs text-gray-400 mb-1.5 uppercase tracking-wider">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter password"
              className="w-full px-3 py-2.5 bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg text-white text-sm focus:outline-none focus:border-red-500 transition-colors"
            />
          </div>

          <div className="mb-6">
            <label className="block text-xs text-gray-400 mb-1.5 uppercase tracking-wider">Login Mode</label>
            <select
              value={loginMode}
              onChange={(e) => setLoginMode(Number(e.target.value))}
              className="w-full px-3 py-2.5 bg-[#0f0f1a] border border-[#2a2a4a] rounded-lg text-white text-sm focus:outline-none focus:border-red-500"
            >
              <option value={1}>Standard (1)</option>
              <option value={8}>Guest (8)</option>
              <option value={16}>LDAP (16)</option>
            </select>
          </div>

          {error && (
            <div className="mb-4 p-3 bg-red-900/30 border border-red-800 rounded-lg text-red-300 text-sm">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 bg-gradient-to-r from-red-600 to-red-700 hover:from-red-700 hover:to-red-800 text-white font-medium rounded-lg transition-all disabled:opacity-50 flex items-center justify-center gap-2"
          >
            <VscKey />
            {loading ? 'Connecting...' : 'Connect'}
          </button>

          <p className="text-xs text-gray-500 mt-4 text-center">
            Connecting to MicroStrategy REST API via proxy
          </p>
        </form>

      </div>
    </div>
  );
}
