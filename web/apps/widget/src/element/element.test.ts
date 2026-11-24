/** <codura-chat> in Shadow DOM: modes, RTL, accessibility hooks, sanitised answers, JS API. */
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { CONFIG, json, mockFetch, sse } from "../test/helpers";
import { CoduraChat, define } from "./index";

const XSS_ANSWER = [
  'event: meta\ndata: {"conversation_id":"c1","message_id":"a1","user_message_id":"u1","language":"en"}\n\n',
  'event: delta\ndata: {"text":"<img src=x onerror=alert(1)> [evil](javascript:alert(1)) **ok**"}\n\n',
  'event: citations\ndata: {"items":[{"n":1,"document_id":"d","title":"<script>alert(1)</script>","page":2,"heading_path":["<b>h</b>"],"snippet":"<img src=x onerror=alert(2)>"}]}\n\n',
  'event: done\ndata: {"finish_reason":"stop","latency_ms":5}\n\n',
].join("");

beforeAll(() => define());
afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  localStorage.clear();
});

async function mount(attrs: Record<string, string>) {
  vi.stubGlobal("fetch", mockFetch({
    "GET /api/v1/channel/config": () => json(CONFIG),
    "POST /api/v1/chat/stream": () => sse(XSS_ANSWER),
  }).fn);
  const el = document.createElement("codura-chat");
  for (const [k, v] of Object.entries({ key: "pk_test_000000000000", api: "https://chat.example.com", ...attrs })) el.setAttribute(k, v);
  document.body.appendChild(el);
  const root = el.shadowRoot!;
  await vi.waitFor(() => expect(root.querySelector(".launcher, .composer")).toBeTruthy());
  return { el, root };
}

describe("<codura-chat>", () => {
  it("renders the launcher into a shadow root and opens a labelled dialog", async () => {
    const { root } = await mount({ mode: "launcher" });
    expect(document.querySelector(".launcher")).toBeNull(); // nothing leaks into the host page
    const launcher = root.querySelector<HTMLButtonElement>(".launcher")!;
    expect(launcher.getAttribute("aria-label")).toBe("Open chat");
    launcher.click();
    await vi.waitFor(() => expect(root.querySelector('[role="dialog"]')).toBeTruthy());
    expect(root.querySelector('[role="dialog"]')!.getAttribute("aria-label")).toBe("Ask Acme");
    expect(root.querySelector('[aria-live="polite"]')).toBeTruthy();
    expect(root.querySelector(".foot")!.textContent).toContain("Powered by Codura");
  });

  it("inline mode streams a sanitised answer with safe Citations", async () => {
    const { root } = await mount({ mode: "inline" });
    const ta = root.querySelector("textarea")!;
    ta.value = "hello";
    ta.dispatchEvent(new Event("input"));
    await vi.waitFor(() => expect(root.querySelector(".send:not([disabled])")).toBeTruthy());
    root.querySelector<HTMLFormElement>(".composer")!.dispatchEvent(new Event("submit", { cancelable: true }));
    await vi.waitFor(() => expect(root.querySelector(".chip")).toBeTruthy());
    const bubble = root.querySelector(".assistant:not(.welcome) .bubble")!;
    expect(bubble.querySelector("img, script")).toBeNull();
    for (const el of Array.from(bubble.querySelectorAll("*"))) {
      expect(el.getAttribute("onerror")).toBeNull();
      expect(el.getAttribute("href") ?? "").not.toMatch(/^javascript:/i);
    }
    expect(bubble.innerHTML).toContain("<strong>ok</strong>");
    root.querySelector<HTMLButtonElement>(".chip")!.click();
    await vi.waitFor(() => expect(root.querySelector(".pop")).toBeTruthy());
    const pop = root.querySelector(".pop")!;
    expect(pop.querySelector("script, img, b")).toBeNull(); // Citation text is rendered as text
    expect(pop.textContent).toContain("<script>alert(1)</script>");
    expect(root.querySelectorAll('.fb button[aria-pressed]').length).toBe(2);
  });

  it("mirrors the whole UI for RTL locales", async () => {
    const { root } = await mount({ mode: "inline", lang: "ar" });
    expect(root.querySelector(".panel")!.getAttribute("dir")).toBe("rtl");
    expect(root.querySelector(".send")!.textContent).toBe("إرسال");
    expect(root.querySelector("textarea")!.getAttribute("dir")).toBe("auto");
  });

  it("exposes window.CoduraChat", async () => {
    const { root } = await mount({ mode: "launcher" });
    expect((window as unknown as { CoduraChat: unknown }).CoduraChat).toBe(CoduraChat);
    CoduraChat.open();
    await vi.waitFor(() => expect(root.querySelector('[role="dialog"]')).toBeTruthy());
    CoduraChat.close();
    await vi.waitFor(() => expect(root.querySelector(".launcher")).toBeTruthy());
  });
});
