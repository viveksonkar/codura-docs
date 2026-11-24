/** Setup helpers that talk to the stack's HTTP APIs (no browser). */
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { BASE_URL, MAILPIT_URL } from "../playwright.config";

export const STATE_FILE = join(__dirname, "..", ".e2e-state.json");
export const DEMO = { admin: "admin@demo.test", password: "demo-password", widgetKey: "pk_demo_widget_local_only_00000000000000000000000" };

export interface E2EState {
  demoAssistantId: string;
  demoKbId: string;
  other: { key: string; email: string; password: string };
}

export function readState(): E2EState {
  return JSON.parse(readFileSync(STATE_FILE, "utf-8")) as E2EState;
}

export function writeState(s: E2EState): void {
  writeFileSync(STATE_FILE, JSON.stringify(s, null, 2));
}

/** Cookie session client for the Admin API (CSRF header on unsafe methods). */
export class Admin {
  private cookie = "";
  private csrf = "";
  me: Record<string, any> = {};

  static async login(email: string, password: string): Promise<Admin> {
    const a = new Admin();
    const res = await fetch(`${BASE_URL}/api/admin/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) throw new Error(`login ${email}: ${res.status} ${await res.text()}`);
    a.cookie = (res.headers.get("set-cookie") ?? "").split(";")[0]!;
    a.me = (await res.json()) as Record<string, any>;
    a.csrf = a.me.csrf_token as string;
    return a;
  }

  async call<T = any>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Cookie: this.cookie, "X-CSRF-Token": this.csrf };
    let payload: BodyInit | undefined;
    if (body instanceof FormData) payload = body;
    else if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    const res = await fetch(`${BASE_URL}${path}`, { method, headers, body: payload });
    const text = await res.text();
    if (!res.ok) throw new Error(`${method} ${path}: ${res.status} ${text}`);
    return (text ? JSON.parse(text) : undefined) as T;
  }
}

export async function waitFor<T>(fn: () => Promise<T | null | undefined | false>, what: string, timeoutMs = 180_000): Promise<T> {
  const until = Date.now() + timeoutMs;
  while (Date.now() < until) {
    const v = await fn();
    if (v) return v;
    await new Promise((r) => setTimeout(r, 1500));
  }
  throw new Error(`timed out waiting for ${what}`);
}

/** The newest Mailpit message to `to`, its plain text. */
export async function lastMail(to: string): Promise<string> {
  return waitFor(async () => {
    const list = (await (await fetch(`${MAILPIT_URL}/api/v1/search?query=${encodeURIComponent(`to:${to}`)}`)).json()) as {
      messages: { ID: string }[];
    };
    const first = list.messages?.[0];
    if (!first) return null;
    const msg = (await (await fetch(`${MAILPIT_URL}/api/v1/message/${first.ID}`)).json()) as { Text: string };
    return msg.Text;
  }, `email to ${to}`, 60_000);
}

/** Self-service signup + email verification through Mailpit (ADR-0020). */
export async function signupVerified(org: string, email: string, password: string): Promise<Admin> {
  const res = await fetch(`${BASE_URL}/api/auth/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ org_name: org, name: "E2E Owner", email, password }),
  });
  if (res.status !== 202) throw new Error(`signup: ${res.status} ${await res.text()}`);
  const text = await lastMail(email);
  const token = /token=([A-Za-z0-9_\-]+)/.exec(text)?.[1];
  if (!token) throw new Error("no verification token in email");
  const v = await fetch(`${BASE_URL}/api/auth/verify-email`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  if (!v.ok) throw new Error(`verify: ${v.status}`);
  return Admin.login(email, password);
}
