/**
 * @codura/chat-client — zero-dependency client for the Codura Chat API (specs/api.md §3, M1-12).
 * Used by the Admin Playground (session auth) and the Widget / Hosted Chat Page (Channel Key).
 */
import { SseParser } from "./sse";

export { SseParser } from "./sse";
export type { SseMessage } from "./sse";

export const API_VERSION = "v1" as const;

export function apiPath(path: string): string {
  return `/api/${API_VERSION}/${path.replace(/^\/+/, "")}`;
}

export interface Citation {
  n: number;
  document_id: string;
  title: string;
  page: number | null;
  heading_path: string[];
  snippet: string | null;
}

export type ChatEvent =
  | { type: "meta"; conversation_id: string; message_id: string; user_message_id: string; language: string }
  | { type: "delta"; text: string }
  | { type: "citations"; items: Citation[] }
  | { type: "done"; finish_reason: "stop" | "length" | "fallback" | "limit" | "cancelled" | "error"; latency_ms: number }
  | { type: "error"; code: string; message: string }
  | { type: "debug"; standalone_query: string; retrieval: Record<string, unknown>[]; model_profile: string };

const KNOWN = new Set(["meta", "delta", "citations", "done", "error", "debug"]);

export class ChatApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

/** Parse an SSE response body into typed chat events; unknown event types are ignored. */
export async function* readChatEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<ChatEvent> {
  const parser = new SseParser();
  const reader = body.getReader();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) return;
      for (const msg of parser.push(value)) {
        if (!KNOWN.has(msg.event)) continue;
        yield { type: msg.event, ...JSON.parse(msg.data) } as ChatEvent;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

async function errorFrom(res: Response): Promise<ChatApiError> {
  try {
    const body = (await res.json()) as { error?: { code?: string; message?: string } };
    return new ChatApiError(res.status, body.error?.code ?? "error", body.error?.message ?? res.statusText);
  } catch {
    return new ChatApiError(res.status, "error", res.statusText);
  }
}

/** POST a question to any streaming endpoint and yield its events. */
export async function* streamAnswer(
  url: string,
  body: Record<string, unknown>,
  init: { headers?: Record<string, string>; signal?: AbortSignal; credentials?: RequestCredentials } = {},
): AsyncGenerator<ChatEvent> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...(init.headers ?? {}) },
    body: JSON.stringify(body),
    signal: init.signal ?? null,
    credentials: init.credentials ?? "same-origin",
  });
  if (!res.ok || !res.body) throw await errorFrom(res);
  yield* readChatEvents(res.body);
}

/** Minimal storage interface (localStorage by default; every access is guarded). */
export interface KeyValueStore {
  get(key: string): string | null;
  set(key: string, value: string): void;
}

export const safeLocalStorage: KeyValueStore = {
  get(key) {
    try {
      return globalThis.localStorage?.getItem(key) ?? null;
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      globalThis.localStorage?.setItem(key, value);
    } catch {
      /* private mode / blocked storage: chat still works, history is not remembered */
    }
  },
};

function uuid(): string {
  const c = globalThis.crypto;
  if (c?.randomUUID) return c.randomUUID();
  return "v-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
}

export interface ChannelClientOptions {
  apiUrl: string;
  channelKey: string;
  storage?: KeyValueStore;
  clientName?: string;
}

/** Public Chat API client for Channels. Visitor + Conversation ids are stored per Channel Key. */
export class ChannelClient {
  private storage: KeyValueStore;
  constructor(private opts: ChannelClientOptions) {
    this.storage = opts.storage ?? safeLocalStorage;
  }

  private key(name: string): string {
    return `codura:${this.opts.channelKey.slice(0, 16)}:${name}`;
  }

  get visitorId(): string {
    let id = this.storage.get(this.key("visitor"));
    if (!id) {
      id = uuid();
      this.storage.set(this.key("visitor"), id);
    }
    return id;
  }

  get conversationId(): string | null {
    return this.storage.get(this.key("conversation"));
  }

  set conversationId(id: string | null) {
    this.storage.set(this.key("conversation"), id ?? "");
  }

  private headers(): Record<string, string> {
    return {
      Authorization: `Bearer ${this.opts.channelKey}`,
      "X-Codura-Visitor": this.visitorId,
      "X-Codura-Client": this.opts.clientName ?? "chat-client/0.1.0",
    };
  }

  private url(path: string): string {
    return this.opts.apiUrl.replace(/\/+$/, "") + apiPath(path);
  }

  async config(): Promise<Record<string, unknown>> {
    const res = await fetch(this.url("channel/config"), { headers: this.headers() });
    if (!res.ok) throw await errorFrom(res);
    return (await res.json()) as Record<string, unknown>;
  }

  async *ask(question: string, opts: { signal?: AbortSignal; visitorEmail?: string; consent?: boolean } = {}) {
    const body: Record<string, unknown> = { question, conversation_id: this.conversationId || null };
    if (opts.visitorEmail) Object.assign(body, { visitor_email: opts.visitorEmail, consent: opts.consent ?? false });
    for await (const event of streamAnswer(this.url("chat/stream"), body, {
      headers: this.headers(),
      credentials: "omit",
      ...(opts.signal ? { signal: opts.signal } : {}),
    })) {
      if (event.type === "meta") this.conversationId = event.conversation_id;
      yield event;
    }
  }

  async feedback(messageId: string, value: 1 | -1, note?: string): Promise<void> {
    const res = await fetch(this.url(`messages/${messageId}/feedback`), {
      method: "POST",
      headers: { ...this.headers(), "Content-Type": "application/json" },
      body: JSON.stringify({ value, note }),
    });
    if (!res.ok) throw await errorFrom(res);
  }
}
