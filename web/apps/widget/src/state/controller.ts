/**
 * Chat state for one <codura-chat> instance (channels.md §1). Framework-free so it can be unit
 * tested and drive the JS API. Visitor + Conversation ids are stored per Channel Key, so two
 * Tenants on one device never share identity or history.
 */
import {
  ChannelClient,
  ChatApiError,
  type ChannelConfig,
  type Citation,
  type KeyValueStore,
} from "@codura/chat-client";
import { resolveLang } from "../i18n";

export type Role = "user" | "assistant";
export type MessageStatus = "streaming" | "done" | "error" | "cancelled";

export interface ChatMessage {
  id: string;
  role: Role;
  text: string;
  citations: Citation[];
  status: MessageStatus;
  finish?: string;
  feedback?: 1 | -1 | null;
  language?: string | null;
  error?: string; // i18n key
  question?: string; // assistant messages: the question, for retry
}

export type Phase = "loading" | "unavailable" | "prechat" | "ready";

export interface ChatState {
  phase: Phase;
  config: ChannelConfig | null;
  messages: ChatMessage[];
  streaming: boolean;
  offline: boolean;
  notice: string | null; // i18n key of a transient banner
  lang: string;
  open: boolean;
}

export interface ControllerOptions {
  apiUrl: string;
  channelKey: string;
  lang?: string | null;
  storage?: KeyValueStore;
  clientName?: string;
}

type Listener = (s: ChatState) => void;
type EventName = "message" | "open" | "close" | "ready";
type EventHandler = (detail: unknown) => void;

const EMAIL = /^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$/;
let seq = 0;
const localId = (p: string) => `${p}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

export function validEmail(email: string): boolean {
  return EMAIL.test(email.trim());
}

export class ChatController {
  readonly client: ChannelClient;
  private state: ChatState;
  private listeners = new Set<Listener>();
  private handlers = new Map<EventName, Set<EventHandler>>();
  private abort: AbortController | null = null;
  private pendingEmail: { email: string; consent: boolean } | null = null;

  constructor(private opts: ControllerOptions) {
    const lang = resolveLang(opts.lang);
    this.client = new ChannelClient({
      apiUrl: opts.apiUrl,
      channelKey: opts.channelKey,
      clientName: opts.clientName ?? "widget/1.0.0",
      language: lang,
      ...(opts.storage ? { storage: opts.storage } : {}),
    });
    this.state = {
      phase: "loading",
      config: null,
      messages: [],
      streaming: false,
      offline: globalThis.navigator?.onLine === false,
      notice: null,
      lang,
      open: false,
    };
  }

  // --- subscription -------------------------------------------------------------------------

  get(): ChatState {
    return this.state;
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  on(event: EventName, fn: EventHandler): () => void {
    let set = this.handlers.get(event);
    if (!set) this.handlers.set(event, (set = new Set()));
    set.add(fn);
    return () => set!.delete(fn);
  }

  private emit(event: EventName, detail: unknown): void {
    this.handlers.get(event)?.forEach((fn) => {
      try {
        fn(detail);
      } catch {
        /* a host page handler must never break the widget */
      }
    });
  }

  private set(patch: Partial<ChatState>): void {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((fn) => fn(this.state));
  }

  private patchMessage(id: string, patch: Partial<ChatMessage>): void {
    this.set({ messages: this.state.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)) });
  }

  // --- lifecycle ----------------------------------------------------------------------------

  async init(): Promise<void> {
    this.set({ phase: "loading", notice: null });
    let config: ChannelConfig;
    try {
      config = await this.client.config();
    } catch (e) {
      this.set({ phase: "unavailable", notice: e instanceof ChatApiError && e.status === 429 ? "rate_limited" : null });
      return;
    }
    let messages: ChatMessage[] = [];
    try {
      const history = await this.client.history();
      messages = history.map((h) => ({
        id: h.id,
        role: h.role,
        text: h.content,
        citations: h.citations ?? [],
        status: "done" as const,
        finish: h.finish_reason ?? "stop",
        feedback: h.feedback,
        language: h.language,
      }));
    } catch {
      /* history is a convenience; a failure starts a fresh view */
    }
    const needsForm = config.visitor_email.mode !== "off" && !this.client.conversationId && !this.client.getFlag("prechat");
    this.set({ config, messages, phase: needsForm ? "prechat" : "ready" });
    this.emit("ready", { assistant: config.assistant.name });
  }

  setOffline(offline: boolean): void {
    this.set({ offline, notice: offline ? "offline" : this.state.notice === "offline" ? null : this.state.notice });
  }

  setOpen(open: boolean): void {
    if (open === this.state.open) return;
    this.set({ open });
    this.emit(open ? "open" : "close", {});
  }

  async setLanguage(code: string): Promise<void> {
    const lang = resolveLang(code);
    this.client.language = lang;
    this.set({ lang });
    if (this.state.phase !== "loading") {
      try {
        this.set({ config: await this.client.config() }); // welcome + consent text for the locale
      } catch {
        /* keep the previous config */
      }
    }
  }

  // --- pre-chat email (ADR-0023) ----------------------------------------------------------------

  submitPrechat(email: string | null, consent: boolean): "ok" | "prechat_invalid" | "prechat_consent_required" {
    const mode = this.state.config?.visitor_email.mode ?? "off";
    const value = (email ?? "").trim();
    if (!value) {
      if (mode === "required") return "prechat_invalid";
      this.pendingEmail = null;
    } else {
      if (!validEmail(value)) return "prechat_invalid";
      if (!consent) return "prechat_consent_required";
      this.pendingEmail = { email: value, consent };
    }
    this.client.setFlag("prechat", "1");
    this.set({ phase: "ready" });
    return "ok";
  }

  // --- chatting -------------------------------------------------------------------------------

  async send(question: string): Promise<void> {
    const text = question.trim();
    const max = this.state.config?.limits.max_question_chars ?? 2000;
    if (!text || this.state.streaming || this.state.phase !== "ready") return;
    if (this.state.offline) {
      this.set({ notice: "offline" });
      return;
    }
    const userMsg: ChatMessage = { id: localId("u"), role: "user", text: text.slice(0, max), citations: [], status: "done" };
    const answer: ChatMessage = { id: localId("a"), role: "assistant", text: "", citations: [], status: "streaming", question: userMsg.text };
    this.set({ messages: [...this.state.messages, userMsg, answer], streaming: true, notice: null });
    this.emit("message", { role: "user", text: userMsg.text });
    await this.stream(answer.id, userMsg);
  }

  private async stream(answerId: string, userMsg: ChatMessage): Promise<void> {
    const abort = new AbortController();
    this.abort = abort;
    let id = answerId;
    let text = "";
    const email = this.client.conversationId ? null : this.pendingEmail;
    try {
      for await (const ev of this.client.ask(userMsg.text, {
        signal: abort.signal,
        ...(email ? { visitorEmail: email.email, consent: email.consent } : {}),
      })) {
        if (ev.type === "meta") {
          this.pendingEmail = null;
          this.patchMessage(userMsg.id, { id: ev.user_message_id, language: ev.language });
          this.patchMessage(id, { id: ev.message_id, language: ev.language });
          id = ev.message_id;
        } else if (ev.type === "delta") {
          text += ev.text;
          this.patchMessage(id, { text });
        } else if (ev.type === "citations") {
          this.patchMessage(id, { citations: ev.items });
        } else if (ev.type === "done") {
          this.patchMessage(id, { status: ev.finish_reason === "cancelled" ? "cancelled" : "done", finish: ev.finish_reason });
        } else if (ev.type === "error") {
          this.patchMessage(id, { status: "error", error: "error" });
        }
      }
      const final = this.state.messages.find((m) => m.id === id);
      if (final?.status === "streaming") this.patchMessage(id, { status: "done", finish: "stop" });
      if (final) this.emit("message", { role: "assistant", text: final.text, citations: final.citations });
    } catch (e) {
      if (abort.signal.aborted) {
        this.patchMessage(id, { status: "cancelled", finish: "cancelled" });
      } else {
        const code = e instanceof ChatApiError ? e.code : "";
        const key = code === "rate_limited" ? "rate_limited" : code === "visitor_email_required" ? "prechat_invalid" : globalThis.navigator?.onLine === false ? "offline" : "error";
        this.patchMessage(id, { status: "error", error: key });
        if (code === "visitor_email_required") {
          this.client.setFlag("prechat", null);
          this.set({ phase: "prechat" });
        }
        if (code === "not_found") this.client.conversationId = null; // conversation gone: next send starts fresh
      }
    } finally {
      if (this.abort === abort) this.abort = null;
      this.set({ streaming: false });
    }
  }

  stop(): void {
    this.abort?.abort();
  }

  async retry(answerId: string): Promise<void> {
    const msgs = this.state.messages;
    const i = msgs.findIndex((m) => m.id === answerId);
    const failed = msgs[i];
    if (!failed || failed.role !== "assistant" || this.state.streaming) return;
    const question = failed.question ?? msgs[i - 1]?.text;
    if (!question) return;
    // Drop the failed pair and ask again.
    this.set({ messages: msgs.filter((_, j) => j !== i && !(j === i - 1 && msgs[j]?.role === "user")) });
    await this.send(question);
  }

  async feedback(messageId: string, value: 1 | -1): Promise<void> {
    const msg = this.state.messages.find((m) => m.id === messageId);
    if (!msg || msg.role !== "assistant" || msg.id.startsWith("a-")) return;
    const previous = msg.feedback ?? null;
    this.patchMessage(messageId, { feedback: value });
    try {
      await this.client.feedback(messageId, value);
    } catch {
      this.patchMessage(messageId, { feedback: previous });
      this.set({ notice: "error" });
    }
  }

  newConversation(): void {
    this.stop();
    this.client.conversationId = null;
    const needsForm = this.state.config?.visitor_email.mode === "required";
    if (needsForm) this.client.setFlag("prechat", null);
    this.set({ messages: [], notice: null, phase: needsForm ? "prechat" : this.state.phase === "unavailable" ? "unavailable" : "ready" });
  }
}
