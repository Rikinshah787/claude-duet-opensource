import type {
  AgentsResponse,
  Approval,
  ApprovalActionResult,
  ApprovalsResponse,
  AdminResetResponse,
  AuditResponse,
  AuditVerifyResponse,
  Briefing,
  BriefingResponse,
  Connection,
  ConnectorSyncResponse,
  ConnectRequest,
  ConnectorsResponse,
  DashboardResponse,
  Health,
  Me,
  OkResponse,
  PostThreadResponse,
  PriorityRule,
  QuietHours,
  RuleKind,
  SettingsResponse,
  SignalsResponse,
  SyncAllResponse,
  Task,
  TaskDetail,
  TasksResponse,
  ThreadResponse,
} from './types';

export const API_BASE: string = process.env.NEXT_PUBLIC_API_BASE ?? 'http://127.0.0.1:8787';

export class ApiError extends Error {
  readonly status: number;
  readonly body: string;

  constructor(status: number, body: string, path: string) {
    super(`${status} on ${path}${body ? ` - ${body.slice(0, 180)}` : ''}`);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

/**
 * The message the service actually sent, where it sent one.
 *
 * FastAPI answers errors with `{"detail": …}`; `ApiError.message` wraps that in
 * status and path noise, which is the wrong thing to show a person who just
 * mistyped a token. Falls back to the generic message.
 */
export function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.body) {
    try {
      const parsed: unknown = JSON.parse(error.body);
      if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
        const detail = (parsed as { detail: unknown }).detail;
        if (typeof detail === 'string' && detail.trim()) return detail;
        // Pydantic validation errors arrive as a list of objects.
        if (Array.isArray(detail)) {
          const messages = detail
            .map((entry) =>
              entry && typeof entry === 'object' && typeof (entry as { msg?: unknown }).msg === 'string'
                ? (entry as { msg: string }).msg
                : null,
            )
            .filter((msg): msg is string => msg !== null);
          if (messages.length > 0) return messages.join('; ');
        }
      }
    } catch {
      // Not JSON; fall through to the generic message.
    }
  }
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  return 'Something went wrong.';
}

function resolve(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  const normalised = path.startsWith('/') ? path : `/${path}`;
  // In the browser we stay same-origin; next.config.mjs rewrites /api/* to the
  // Python service. On the server there is no origin, so address it directly.
  return typeof window === 'undefined' ? `${API_BASE}${normalised}` : normalised;
}

/** Typed fetch wrapper. Throws `ApiError` on any non-2xx response. */
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  headers.set('Accept', 'application/json');

  const res = await fetch(resolve(path), { ...init, headers, cache: 'no-store' });

  if (!res.ok) {
    let body = '';
    try {
      body = await res.text();
    } catch {
      body = '';
    }
    throw new ApiError(res.status, body, path);
  }

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
}

function patch<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, { method: 'PATCH', body: JSON.stringify(body) });
}

function del<T>(path: string): Promise<T> {
  return api<T>(path, { method: 'DELETE' });
}

function qs(params: Record<string, string | number | undefined>): string {
  const out = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') out.set(key, String(value));
  }
  const s = out.toString();
  return s ? `?${s}` : '';
}

export const duet = {
  health: () => api<Health>('/api/health'),
  me: () => api<Me>('/api/me'),

  thread: (limit = 100) => api<ThreadResponse>(`/api/thread${qs({ limit })}`),
  say: (text: string) => post<PostThreadResponse>('/api/thread', { text }),

  latestBriefing: () => api<BriefingResponse>('/api/briefing/latest'),
  generateBriefing: () => post<{ briefing: Briefing }>('/api/briefing/generate'),

  signals: (disposition?: string, limit = 50) =>
    api<SignalsResponse>(`/api/signals${qs({ disposition, limit })}`),
  dismissSignal: (id: string) => post<OkResponse>(`/api/signals/${id}/dismiss`),
  handleSignal: (id: string) => post<OkResponse>(`/api/signals/${id}/handle`),

  tasks: (status?: string, limit = 50) => api<TasksResponse>(`/api/tasks${qs({ status, limit })}`),
  task: (id: string) => api<TaskDetail>(`/api/tasks/${id}`),
  createTask: (intent: string) => post<{ task: Task }>('/api/tasks', { intent }),

  approvals: (status = 'pending') => api<ApprovalsResponse>(`/api/approvals${qs({ status })}`),
  approve: (id: string, note?: string) =>
    post<ApprovalActionResult>(`/api/approvals/${id}/approve`, note ? { note } : {}),
  reject: (id: string, note?: string) =>
    post<{ approval: Approval }>(`/api/approvals/${id}/reject`, note ? { note } : {}),
  revert: (id: string) => post<{ approval: Approval }>(`/api/approvals/${id}/revert`),

  connectors: () => api<ConnectorsResponse>('/api/connectors'),
  /** Pull one connected app right now. */
  syncConnector: (key: string) => post<ConnectorSyncResponse>(`/api/connectors/${key}/sync`),
  /**
   * The credential travels in the POST body — never the path, never a query
   * string — so it stays out of access logs and browser history.
   */
  connect: (key: string, body: ConnectRequest = {}) =>
    post<{ connection: Connection }>(`/api/connectors/${key}/connect`, body),
  disconnect: (key: string) => post<OkResponse>(`/api/connectors/${key}/disconnect`),

  /** Returns the provider URL to open. The browser does the redirect dance. */
  oauthStart: (key: string) =>
    post<{ url: string; state: string; provider: string; expires_in: number }>(
      `/api/oauth/${key}/start`,
    ),
  updateConnector: (key: string, body: { monitored?: boolean; enabled?: boolean }) =>
    patch<{ connection: Connection }>(`/api/connectors/${key}`, body),

  agents: () => api<AgentsResponse>('/api/agents'),
  dashboard: () => api<DashboardResponse>('/api/dashboards/executive'),

  audit: (limit = 100) => api<AuditResponse>(`/api/audit${qs({ limit })}`),
  auditVerify: () => api<AuditVerifyResponse>('/api/audit/verify'),

  settings: () => api<SettingsResponse>('/api/settings'),
  updateSettings: (body: { quiet_hours?: QuietHours; briefing_hour?: number }) =>
    patch<OkResponse>('/api/settings', body),
  addRule: (body: { kind: RuleKind; pattern: string; weight?: number; note?: string }) =>
    post<{ rule: PriorityRule }>('/api/settings/rules', body),
  deleteRule: (id: string) => del<OkResponse>(`/api/settings/rules/${id}`),

  /** Sweep every connected app now, rather than waiting for the schedule. */
  sync: () => post<SyncAllResponse>('/api/sync'),

  /**
   * Erases everything this account holds and disconnects every app, destroying
   * the stored credentials with it. Irreversible; only ever called from the
   * two-step confirmation in Settings.
   */
  adminReset: () => post<AdminResetResponse>('/api/admin/reset'),
};

/** React Query keys, kept in one place so mutations can invalidate precisely. */
export const qk = {
  health: ['health'] as const,
  me: ['me'] as const,
  thread: ['thread'] as const,
  briefing: ['briefing'] as const,
  signals: ['signals'] as const,
  tasks: ['tasks'] as const,
  task: (id: string) => ['task', id] as const,
  approvals: (status: string) => ['approvals', status] as const,
  connectors: ['connectors'] as const,
  agents: ['agents'] as const,
  dashboard: ['dashboard'] as const,
  audit: ['audit'] as const,
  auditVerify: ['audit', 'verify'] as const,
  settings: ['settings'] as const,
};
