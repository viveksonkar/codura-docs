/**
 * <codura-chat> Custom Element: Preact inside Shadow DOM (NFR-08, ADR-0010).
 *
 *   <codura-chat key="pk_…" api="https://chat.example.com" mode="inline|launcher"
 *                position="right|left" lang="auto|hi|…" title="Ask us" theme="light|dark"></codura-chat>
 *
 * JS API: window.CoduraChat.open() / close() / send(text) / setLanguage(code) / on(event, fn).
 */
import { render } from "preact";
import { App } from "../components/App";
import { STYLES } from "../components/styles";
import { ChatController } from "../state/controller";

export const VERSION = "1.0.0";
const OBSERVED = ["key", "api", "mode", "position", "lang", "title", "accent", "fullpage", "hide-header"] as const;

export class CoduraChatElement extends HTMLElement {
  static observedAttributes = [...OBSERVED];
  controller: ChatController | null = null;
  private root: ShadowRoot;
  private mount: HTMLDivElement;
  private connected = false;
  private cleanup: (() => void)[] = [];

  constructor() {
    super();
    this.root = this.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = STYLES;
    this.mount = document.createElement("div");
    this.root.append(style, this.mount);
  }

  connectedCallback(): void {
    this.connected = true;
    this.start();
  }

  disconnectedCallback(): void {
    this.connected = false;
    this.controller?.stop();
    this.cleanup.forEach((fn) => fn());
    this.cleanup = [];
    render(null, this.mount);
  }

  attributeChangedCallback(name: string, oldValue: string | null, value: string | null): void {
    if (!this.connected || oldValue === value) return;
    if (name === "key" || name === "api") this.start();
    else if (name === "lang" && this.controller) void this.controller.setLanguage(value ?? "auto");
    else this.renderApp();
    if (name === "accent") this.applyAccent();
  }

  private start(): void {
    const key = this.getAttribute("key") ?? "";
    if (!key.startsWith("pk_")) {
      console.warn("[codura-chat] a Channel Key (pk_…) is required");
      return;
    }
    this.controller?.stop();
    this.cleanup.forEach((fn) => fn());
    const api = this.getAttribute("api") || defaultApi();
    const controller = new ChatController({ apiUrl: api, channelKey: key, lang: this.getAttribute("lang") });
    this.controller = controller;
    const online = () => controller.setOffline(false);
    const offline = () => controller.setOffline(true);
    window.addEventListener("online", online);
    window.addEventListener("offline", offline);
    this.cleanup = [
      () => window.removeEventListener("online", online),
      () => window.removeEventListener("offline", offline),
      controller.on("message", (detail) => this.dispatchEvent(new CustomEvent("codura:message", { detail }))),
      controller.on("ready", (detail) => this.dispatchEvent(new CustomEvent("codura:ready", { detail }))),
    ];
    this.applyAccent();
    this.renderApp();
    void controller.init().then(() => {
      const accent = controller.get().config?.theme.accent;
      if (!this.getAttribute("accent") && typeof accent === "string" && /^#[0-9a-f]{3,8}$/i.test(accent)) {
        this.style.setProperty("--codura-accent", accent);
      }
    });
  }

  private applyAccent(): void {
    const accent = this.getAttribute("accent");
    if (accent && /^#[0-9a-f]{3,8}$/i.test(accent)) this.style.setProperty("--codura-accent", accent);
  }

  private renderApp(): void {
    if (!this.controller) return;
    const mode = this.getAttribute("mode") === "inline" ? "inline" : "launcher";
    const position = this.getAttribute("position") === "left" ? "left" : "right";
    render(
      <App
        controller={this.controller}
        mode={mode}
        position={position}
        title={this.getAttribute("title")}
        fullpage={this.hasAttribute("fullpage")}
        hideHeader={this.hasAttribute("hide-header")}
      />,
      this.mount,
    );
  }
}

function defaultApi(): string {
  const script = document.currentScript as HTMLScriptElement | null;
  return script?.src ? new URL(script.src).origin : window.location.origin;
}

// --- JS API on window ---------------------------------------------------------------------------

function first(): CoduraChatElement | null {
  return document.querySelector("codura-chat") as CoduraChatElement | null;
}

export const CoduraChat = {
  version: VERSION,
  open: () => first()?.controller?.setOpen(true),
  close: () => first()?.controller?.setOpen(false),
  send: (text: string) => {
    const c = first()?.controller;
    c?.setOpen(true);
    return c?.send(text);
  },
  setLanguage: (code: string) => first()?.setAttribute("lang", code),
  on: (event: "message" | "open" | "close" | "ready", fn: (detail: unknown) => void) => first()?.controller?.on(event, fn),
  newConversation: () => first()?.controller?.newConversation(),
};

export function define(): void {
  if (!customElements.get("codura-chat")) customElements.define("codura-chat", CoduraChatElement);
  (window as unknown as { CoduraChat: typeof CoduraChat }).CoduraChat = CoduraChat;
}

/** `<script src=".../widget.js" data-key="pk_…" data-mode=… data-position=… data-lang=…>` auto-mounts. */
export function autoMount(script: HTMLScriptElement | null): void {
  const key = script?.dataset.key;
  if (!script || !key) return;
  const mountNow = () => {
    if (document.querySelector("codura-chat[data-auto]")) return;
    const el = document.createElement("codura-chat");
    el.setAttribute("data-auto", "");
    el.setAttribute("key", key);
    el.setAttribute("api", script.dataset.api || new URL(script.src, window.location.href).origin);
    for (const [attr, value] of Object.entries({
      mode: script.dataset.mode,
      position: script.dataset.position,
      lang: script.dataset.lang,
      title: script.dataset.title,
      accent: script.dataset.accent,
    })) {
      if (value) el.setAttribute(attr, value);
    }
    document.body.appendChild(el);
  };
  if (document.body) mountNow();
  else document.addEventListener("DOMContentLoaded", mountNow, { once: true });
}
