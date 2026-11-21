/** Admin/Platform API client: same-origin session cookie + CSRF header on unsafe methods (SEC-01). */
export interface TenantRef {
  id: string;
  name: string;
  slug: string;
  role: "admin" | "user";
  status: string;
}

export interface Me {
  user: { id: string; email: string; name: string; is_platform_admin: boolean; email_verified: boolean };
  csrf_token: string;
  current_tenant: TenantRef | null;
  tenants: TenantRef[];
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

let csrf = "";
export function setCsrf(token: string): void {
  csrf = token;
}
export function getCsrf(): string {
  return csrf;
}

export async function api<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  const res = await fetch(path, { method, headers, body: payload, credentials: "same-origin" });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string; details?: unknown } }).error ?? {};
    throw new ApiError(res.status, err.code ?? "error", err.message ?? res.statusText, err.details);
  }
  return data as T;
}

export const get = <T,>(p: string) => api<T>("GET", p);
export const post = <T,>(p: string, b?: unknown) => api<T>("POST", p, b ?? {});
export const patch = <T,>(p: string, b: unknown) => api<T>("PATCH", p, b);
export const del = (p: string) => api<void>("DELETE", p);

export const LANGUAGES: Record<string, string> = {
  en: "English", hi: "Hindi", es: "Spanish", fr: "French", de: "German", ar: "Arabic", zh: "Chinese",
};
