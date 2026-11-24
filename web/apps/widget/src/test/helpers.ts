import type { KeyValueStore } from "@codura/chat-client";

/** Shared SSE fixtures (docs/specs/fixtures/sse), also used by the Android client (M4). */
const FIXTURES = import.meta.glob("../../../../../docs/specs/fixtures/sse/*.txt", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

export function fixture(name: string): string {
  const hit = Object.entries(FIXTURES).find(([path]) => path.endsWith(`/${name}`));
  if (!hit) throw new Error(`missing SSE fixture ${name}`);
  return hit[1].replace(/\r\n/g, "\n");
}

export function memoryStore(): KeyValueStore & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, get: (k) => data.get(k) ?? null, set: (k, v) => void data.set(k, v) };
}

export const CONFIG = {
  assistant: { id: "asst_1", name: "Acme Help", welcome_text: "Hi! Ask me.", default_language: "en" },
  theme: { title: "Ask Acme" },
  ui_languages: ["en", "hi", "ar"],
  features: { feedback: true, citations: true },
  visitor_email: { mode: "off", consent_text: "", privacy_url: null },
  branding: true,
  limits: { max_question_chars: 2000 },
};

export interface Call {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
}

type Handler = (call: Call, init: RequestInit) => Response | Promise<Response>;

/** Route fetch() by "METHOD /path-suffix". Records every call. */
export function mockFetch(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const fn = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? "GET").toUpperCase();
    const headers = Object.fromEntries(Object.entries((init.headers as Record<string, string>) ?? {}).map(([k, v]) => [k.toLowerCase(), v]));
    const call = { method, url, headers, body: init.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const path = new URL(url).pathname;
    const key = Object.keys(routes).find((k) => {
      const [m, p] = k.split(" ");
      return m === method && (p!.endsWith("*") ? path.startsWith(p!.slice(0, -1)) : path === p);
    });
    if (!key) return new Response(JSON.stringify({ error: { code: "not_found", message: "nf" } }), { status: 404 });
    return routes[key]!(call, init);
  };
  return { fn: fn as typeof fetch, calls };
}

export const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
export const sse = (text: string) => new Response(text, { status: 200, headers: { "Content-Type": "text/event-stream" } });

/** An SSE response that streams the first chunk, then waits until aborted. */
export function hangingSse(first: string, signal?: AbortSignal | null): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(enc.encode(first));
      signal?.addEventListener("abort", () => controller.error(new DOMException("aborted", "AbortError")));
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

export const tick = () => new Promise((r) => setTimeout(r, 0));
