export type User = {
  id: string;
  email: string;
  name?: string;
  display_name?: string;
  role?: string;
  roles?: string[];
  plan?: string | { name?: string; id?: string };
  status?: string;
  is_active?: boolean;
  limits?: Record<string, unknown>;
  limit_overrides?: Record<string, unknown>;
  limitOverrides?: Record<string, unknown>;
  createdAt?: string;
  created_at?: string;
  emailVerified?: boolean;
  email_verified?: boolean;
};

export type Plan = {
  id: string;
  name: string;
  description?: string;
  price?: number | string | null;
  currency?: string;
  interval?: string;
  limits?: Record<string, unknown>;
  features?: string[];
  active?: boolean;
  is_active?: boolean;
  [key: string]: unknown;
};

export type ApiKey = { id: string; name?: string; prefix?: string; createdAt?: string; created_at?: string; lastUsedAt?: string; last_used_at?: string; revoked?: boolean };
export type Conversion = { id: string; status: string; errorCode?: string; error_code?: string; createdAt?: string; created_at?: string; progress?: number; inputBytes?: number; input_bytes?: number; outputCharCount?: number; output_char_count?: number };

const API = '/v1';
let csrfToken: string | null = null;

function normalizeError(data: unknown, fallback: string): string {
  if (data && typeof data === 'object') {
    const body = data as Record<string, unknown>;
    const detail = body.detail ?? body.message ?? body.error;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object') {
      const d = detail as Record<string, unknown>;
      if (typeof d.message === 'string') return d.message;
      if (typeof d.code === 'string') return d.code;
    }
  }
  return fallback;
}

async function request<T>(path: string, init: RequestInit = {}, mutation = false): Promise<T> {
  const headers = new Headers(init.headers);
  if (mutation) {
    headers.set('X-CSRF-Token', await getCsrfToken());
  }
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${API}${path}`, { ...init, headers, credentials: 'same-origin' });
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get('content-type') ?? '';
  const data = contentType.includes('application/json') ? await response.json().catch(() => null) : await response.text().catch(() => '');
  if (!response.ok) throw new Error(normalizeError(data, `Error ${response.status}`));
  return data as T;
}

export async function getCsrfToken(): Promise<string> {
  if (csrfToken) return csrfToken;
  const data = await request<Record<string, unknown>>('/web-auth/csrf');
  const token = data.csrfToken ?? data.csrf_token ?? data.token;
  if (typeof token !== 'string' || !token) throw new Error('No se pudo obtener el token de seguridad.');
  csrfToken = token;
  return token;
}

function rememberCsrf(data: unknown) {
  if (!data || typeof data !== 'object') return;
  const body = data as Record<string, unknown>;
  const token = body.csrf_token ?? body.csrfToken;
  if (typeof token === 'string' && token) csrfToken = token;
}

export const api = {
  login: async (email: string, password: string) => {
    const result = await request<{ user?: User; csrf_token?: string; csrfToken?: string }>('/web-auth/login', { method: 'POST', body: JSON.stringify({ email, password }) });
    rememberCsrf(result);
    return result;
  },
  me: () => request<{ user?: User } | User>('/web-auth/me'),
  logout: async () => {
    try { await request<void>('/web-auth/logout', { method: 'POST', body: '{}' }, true); }
    finally { csrfToken = null; }
  },
  acceptInvitation: async (token: string, password: string) => {
    const result = await request<Record<string, unknown>>('/web-auth/accept-invitation', { method: 'POST', body: JSON.stringify({ token, password }) });
    rememberCsrf(result);
    return result;
  },
  overview: () => request<Record<string, unknown>>('/admin/overview'),
  users: (query = '') => request<unknown>(`/admin/users${query ? `?${query}` : ''}`),
  user: (id: string) => request<User>(`/admin/users/${encodeURIComponent(id)}`),
  publicPlans: () => request<unknown>('/plans'),
  createUser: (data: Record<string, unknown>) => request<User>('/admin/users', { method: 'POST', body: JSON.stringify(data) }, true),
  updateUser: (id: string, data: Record<string, unknown>) => request<User>(`/admin/users/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(data) }, true),
  plans: () => request<unknown>('/admin/plans'),
  createPlan: (data: Record<string, unknown>) => request<Plan>('/admin/plans', { method: 'POST', body: JSON.stringify(data) }, true),
  updatePlan: (id: string, data: Record<string, unknown>) => request<Plan>(`/admin/plans/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(data) }, true),
  report: (query: string) => request<unknown>(`/admin/reports/consumption${query ? `?${query}` : ''}`),
  exportReport: (query: string) => request<unknown>(`/admin/reports/consumption.csv${query ? `?${query}` : ''}`),
  account: () => request<User>('/account/me'),
  updateAccount: (data: Record<string, unknown>) => request<User>('/account/me', { method: 'PATCH', body: JSON.stringify(data) }, true),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<Record<string, unknown> | void>('/account/password', { method: 'POST', body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) }, true),
  keys: () => request<unknown>('/api-keys'),
  createKey: (name: string) => request<Record<string, unknown>>('/api-keys', { method: 'POST', body: JSON.stringify({ name }) }, true),
  revokeKey: (id: string) => request<void>(`/api-keys/${encodeURIComponent(id)}`, { method: 'DELETE' }, true),
  usage: (query = '') => request<unknown>(`/usage${query ? `?${query}` : ''}`),
  upload: (file: File, ocr: boolean, apiKey: string) => {
    const body = new FormData(); body.append('file', file); if (ocr) body.append('ocr', 'true');
    return request<Conversion>('/conversions', { method: 'POST', headers: { 'X-API-Key': apiKey }, body });
  },
  fromUrl: (url: string, ocr: boolean, apiKey: string) => request<Conversion>('/conversions/from-url', { method: 'POST', headers: { 'X-API-Key': apiKey }, body: JSON.stringify({ url, ocr }) }),
  conversion: (id: string, apiKey: string) => request<Conversion>(`/conversions/${encodeURIComponent(id)}`, { headers: { 'X-API-Key': apiKey } }),
  result: (id: string, apiKey: string) => request<unknown>(`/conversions/${encodeURIComponent(id)}/result`, { headers: { 'X-API-Key': apiKey } }),
};

export function records<T>(data: unknown): T[] {
  if (Array.isArray(data)) return data as T[];
  if (data && typeof data === 'object') {
    const body = data as Record<string, unknown>;
    for (const key of ['items', 'results', 'users', 'plans', 'keys', 'records', 'data', 'entries', 'conversions']) if (Array.isArray(body[key])) return body[key] as T[];
  }
  return [];
}

export function value(obj: Record<string, unknown>, ...keys: string[]): unknown {
  for (const key of keys) if (obj[key] !== undefined && obj[key] !== null) return obj[key];
  return undefined;
}
