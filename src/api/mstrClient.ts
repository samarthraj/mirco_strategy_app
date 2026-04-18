import axios from 'axios';
import type { AxiosInstance, InternalAxiosRequestConfig, AxiosResponse } from 'axios';

export interface RequestLog {
  id: string;
  method: string;
  url: string;
  requestHeaders: Record<string, string>;
  requestBody: unknown;
  responseStatus: number;
  responseHeaders: Record<string, string>;
  responseBody: unknown;
  timestamp: Date;
  duration: number;
}

type RequestLogListener = (log: RequestLog) => void;

let authToken: string | null = null;
let projectId: string | null = null;
let baseUrl = 'https://demo.microstrategy.com/MicroStrategyLibrary';
let lastLoginCredentials: { username: string; password: string; loginMode: number } | null = null;
const listeners: RequestLogListener[] = [];

const client: AxiosInstance = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
    Accept: 'application/json',
  },
  withCredentials: true,
});

client.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (authToken) {
    config.headers['X-MSTR-AuthToken'] = authToken;
  }
  if (projectId && !config.headers['X-MSTR-ProjectID']) {
    config.headers['X-MSTR-ProjectID'] = projectId;
  }
  (config as any).__startTime = Date.now();
  return config;
});

// --- 401 auto-retry with re-authentication ---
let isRetrying = false;

client.interceptors.response.use(
  (response: AxiosResponse) => {
    logRequest(response);
    return response;
  },
  async (error) => {
    if (error.response) {
      logRequest(error.response);
    }
    const originalRequest = error.config;
    if (
      error.response?.status === 401 &&
      !originalRequest.__retried &&
      !isRetrying &&
      lastLoginCredentials &&
      !originalRequest.url?.includes('/auth/login')
    ) {
      originalRequest.__retried = true;
      isRetrying = true;
      try {
        const { username, password, loginMode } = lastLoginCredentials;
        const res = await axios.post('/api/auth/login', { username, password, loginMode }, {
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          withCredentials: true,
        });
        const token = res.headers['x-mstr-authtoken'];
        if (token) {
          authToken = token;
          originalRequest.headers['X-MSTR-AuthToken'] = token;
        }
        return client(originalRequest);
      } catch {
        return Promise.reject(error);
      } finally {
        isRetrying = false;
      }
    }
    return Promise.reject(error);
  }
);

function logRequest(response: AxiosResponse) {
  const config = response.config;
  const duration = Date.now() - ((config as any).__startTime || Date.now());
  let parsedBody: unknown;
  try {
    parsedBody = config.data ? (typeof config.data === 'string' ? JSON.parse(config.data) : config.data) : undefined;
  } catch {
    parsedBody = config.data;
  }
  const log: RequestLog = {
    id: crypto.randomUUID(),
    method: (config.method || 'GET').toUpperCase(),
    url: `${config.baseURL || ''}${config.url || ''}`,
    requestHeaders: config.headers as any,
    requestBody: parsedBody,
    responseStatus: response.status,
    responseHeaders: response.headers as any,
    responseBody: response.data,
    timestamp: new Date(),
    duration,
  };
  listeners.forEach((fn) => fn(log));
}

export function onRequestLog(fn: RequestLogListener) {
  listeners.push(fn);
  return () => {
    const idx = listeners.indexOf(fn);
    if (idx >= 0) listeners.splice(idx, 1);
  };
}

export function setAuthToken(token: string | null) {
  authToken = token;
}

export function getAuthToken() {
  return authToken;
}

export function setProjectId(id: string | null) {
  projectId = id;
}

export function getProjectId() {
  return projectId;
}

export function getBaseUrl() {
  return baseUrl;
}

export async function setBaseUrl(url: string) {
  baseUrl = url.replace(/\/+$/, '');
  // Tell the Vite proxy to forward to the new target
  await fetch('/__proxy_target', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target: baseUrl }),
  });
}

// --- Scoped requests (temporary project override) ---
export async function searchObjectsInProject(projectIdOverride: string, params: {
  name?: string; type?: number; pattern?: number; root?: string;
  offset?: number; limit?: number; getAncestors?: boolean; certifiedStatus?: string;
}) {
  const res = await client.get('/searches/results', {
    params: { ...params, offset: params.offset ?? 0, limit: params.limit ?? 50 },
    headers: { 'X-MSTR-ProjectID': projectIdOverride },
  });
  return res.data;
}

export async function getObjectInfoInProject(projectIdOverride: string, objectId: string, type?: number) {
  const params: any = {};
  if (type !== undefined) params.type = type;
  const res = await client.get(`/objects/${objectId}`, {
    params,
    headers: { 'X-MSTR-ProjectID': projectIdOverride },
  });
  return res.data;
}

export async function getObjectDependenciesInProject(projectIdOverride: string, objectId: string) {
  const res = await client.get(`/objects/${objectId}/dependencies`, {
    headers: { 'X-MSTR-ProjectID': projectIdOverride },
  });
  return res.data;
}

export async function createReportInstanceForSqlInProject(projectIdOverride: string, reportId: string) {
  const res = await client.post(`/v2/reports/${reportId}/instances`, null, {
    params: { executionStage: 'resolve_prompts' },
    headers: { 'X-MSTR-ProjectID': projectIdOverride },
  });
  return res.data;
}

export async function getReportSqlViewInProject(projectIdOverride: string, reportId: string, instanceId: string) {
  const res = await client.get(`/v2/reports/${reportId}/instances/${instanceId}/sqlView`, {
    headers: { 'X-MSTR-ProjectID': projectIdOverride },
  });
  return res.data;
}

export async function getV2ReportDefinitionInProject(projectIdOverride: string, reportId: string) {
  const res = await client.get(`/v2/reports/${reportId}`, {
    headers: { 'X-MSTR-ProjectID': projectIdOverride },
  });
  return res.data;
}

export async function getV2CubeDefinitionInProject(projectIdOverride: string, cubeId: string) {
  const res = await client.get(`/v2/cubes/${cubeId}`, {
    headers: { 'X-MSTR-ProjectID': projectIdOverride },
  });
  return res.data;
}

// --- Auth ---
export async function login(username: string, password: string, loginMode?: number) {
  const mode = loginMode ?? 1;
  const res = await client.post('/auth/login', { username, password, loginMode: mode });
  const token = res.headers['x-mstr-authtoken'];
  if (token) {
    setAuthToken(token);
  }
  lastLoginCredentials = { username, password, loginMode: mode };
  return res.data;
}

export async function logout() {
  try {
    await client.post('/auth/logout');
  } finally {
    setAuthToken(null);
    setProjectId(null);
    lastLoginCredentials = null;
  }
}

export async function getSessionInfo() {
  const res = await client.get('/sessions');
  return res.data;
}

// --- Projects ---
export async function getProjects() {
  const res = await client.get('/projects');
  return res.data;
}

// --- Folders ---
export async function getFolderContents(folderId?: string, offset = 0, limit = 50) {
  const path = folderId ? `/folders/${folderId}` : '/folders/myPersonalObjects';
  const res = await client.get(path, { params: { offset, limit } });
  return res.data;
}

export async function getPredefinedFolders() {
  const res = await client.get('/folders/preDefined');
  return res.data;
}

// --- Search ---
export async function searchObjects(params: {
  name?: string;
  type?: number;
  pattern?: number;
  root?: string;
  offset?: number;
  limit?: number;
  getAncestors?: boolean;
  certifiedStatus?: string;
}) {
  const res = await client.get('/searches/results', {
    params: { ...params, offset: params.offset ?? 0, limit: params.limit ?? 50 },
  });
  return res.data;
}

// --- V2 Definitions ---
export async function getV2ReportDefinition(reportId: string) {
  const res = await client.get(`/v2/reports/${reportId}`);
  return res.data;
}

export async function getV2CubeDefinition(cubeId: string) {
  const res = await client.get(`/v2/cubes/${cubeId}`);
  return res.data;
}

// --- V2 Instances (with requestedObjects, viewFilter, metricLimits, sorting) ---
export interface V2InstanceBody {
  requestedObjects?: {
    attributes?: { id: string }[];
    metrics?: { id: string }[];
  };
  viewFilter?: any;
  metricLimits?: Record<string, any>;
  sorting?: any[];
}

export async function createV2ReportInstance(reportId: string, body?: V2InstanceBody, offset = 0, limit = 100) {
  const res = await client.post(`/v2/reports/${reportId}/instances`, body || {}, {
    params: { offset, limit },
  });
  return res.data;
}

export async function createV2CubeInstance(cubeId: string, body?: V2InstanceBody, offset = 0, limit = 100) {
  const res = await client.post(`/v2/cubes/${cubeId}/instances`, body || {}, {
    params: { offset, limit },
  });
  return res.data;
}

export async function getV2ReportInstanceData(reportId: string, instanceId: string, offset = 0, limit = 100) {
  const res = await client.get(`/v2/reports/${reportId}/instances/${instanceId}`, {
    params: { offset, limit },
  });
  return res.data;
}

export async function getV2CubeInstanceData(cubeId: string, instanceId: string, offset = 0, limit = 100) {
  const res = await client.get(`/v2/cubes/${cubeId}/instances/${instanceId}`, {
    params: { offset, limit },
  });
  return res.data;
}

// --- Attribute Elements (for "select in list" filtering) ---
export async function getAttributeElements(
  objectType: 'cubes' | 'reports',
  objectId: string,
  instanceId: string,
  attributeId: string,
  offset = 0,
  limit = 50,
) {
  const res = await client.get(
    `/${objectType}/${objectId}/instances/${instanceId}/attributes/${attributeId}/elements`,
    { params: { offset, limit } },
  );
  return res.data;
}

// --- Reports (v1) ---
export async function executeReport(reportId: string, offset = 0, limit = 100) {
  const res = await client.post(`/reports/${reportId}/instances`, null, {
    params: { offset, limit },
  });
  return res.data;
}

export async function getReportDefinition(reportId: string) {
  const res = await client.get(`/reports/${reportId}`);
  return res.data;
}

// --- Report Model Definition (rich metadata) ---
export async function getReportModelDefinition(reportId: string) {
  const res = await client.get(`/model/reports/${reportId}`, {
    params: { showExpressionAs: 'tree' },
  });
  return res.data;
}

// --- Cube Model Definition (rich metadata) ---
export async function getCubeModelDefinition(cubeId: string) {
  const res = await client.get(`/model/cubes/${cubeId}`, {
    params: { showExpressionAs: 'tree' },
  });
  return res.data;
}

// --- Report SQL View ---
export async function createReportInstanceForSql(reportId: string) {
  const res = await client.post(`/v2/reports/${reportId}/instances`, null, {
    params: { executionStage: 'resolve_prompts' },
  });
  return res.data;
}

export async function getReportSqlView(reportId: string, instanceId: string) {
  const res = await client.get(`/v2/reports/${reportId}/instances/${instanceId}/sqlView`);
  return res.data;
}

// --- System Hierarchy (attribute parent-child relationships) ---
export async function getSystemHierarchy() {
  const res = await client.get('/model/systemHierarchy');
  return res.data;
}

// --- Cube SQL View ---
export async function createCubeInstanceForSql(cubeId: string) {
  const res = await client.post(`/v2/cubes/${cubeId}/instances`, {});
  return res.data;
}

export async function getCubeSqlView(cubeId: string, instanceId: string) {
  const res = await client.get(`/v2/cubes/${cubeId}/instances/${instanceId}/sqlView`);
  return res.data;
}

export async function getCubeSqlViewDirect(cubeId: string) {
  const res = await client.get(`/v2/cubes/${cubeId}/sqlView`);
  return res.data;
}

// --- Cubes (v1) ---
export async function executeCube(cubeId: string, offset = 0, limit = 100) {
  const res = await client.post(`/cubes/${cubeId}/instances`, null, {
    params: { offset, limit },
  });
  return res.data;
}

export async function getCubeDefinition(cubeId: string) {
  const res = await client.get(`/cubes/${cubeId}`);
  return res.data;
}

// --- Dossiers ---
export async function getDossierDefinition(dossierId: string) {
  const res = await client.get(`/dossiers/${dossierId}/definition`);
  return res.data;
}

// --- Objects ---
export async function getObjectInfo(objectId: string, type?: number) {
  const params: any = {};
  if (type !== undefined) params.type = type;
  const res = await client.get(`/objects/${objectId}`, { params });
  return res.data;
}

export async function getObjectDependencies(objectId: string) {
  const res = await client.get(`/objects/${objectId}/dependencies`);
  return res.data;
}

// --- Monitors / Active Jobs ---
export async function getActiveJobs(offset = 0, limit = 100) {
  const res = await client.get('/monitors/iServer/jobs', {
    params: { offset, limit },
  });
  return res.data;
}

export async function killJob(jobId: number) {
  const res = await client.delete(`/monitors/iServer/jobs/${jobId}`);
  return res.data;
}

export async function getClusterNodes() {
  const res = await client.get('/monitors/iServer/nodes');
  return res.data;
}

// --- Custom request (API Explorer) ---
export async function sendCustomRequest(method: string, path: string, body?: unknown, headers?: Record<string, string>) {
  const res = await client.request({
    method: method as any,
    url: path,
    data: body,
    headers: headers || {},
  });
  return res;
}

export default client;
