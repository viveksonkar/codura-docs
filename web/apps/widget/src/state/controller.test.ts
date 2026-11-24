import { afterEach, describe, expect, it, vi } from "vitest";
import { CONFIG, fixture, hangingSse, json, memoryStore, mockFetch, sse, tick } from "../test/helpers";
import { ChatController } from "./controller";

const API = "https://chat.example.com";

afterEach(() => vi.unstubAllGlobals());

function controller(store = memoryStore(), key = "pk_tenant_a_000000000000", lang = "en") {
  return new ChatController({ apiUrl: API, channelKey: key, lang, storage: store });
}

describe("ChatController", () => {
  it("streams a cited answer from the shared fixture and remembers the Conversation", async () => {
    const m = mockFetch({
      "GET /api/v1/channel/config": () => json(CONFIG),
      "POST /api/v1/chat/stream": () => sse(fixture("answer.txt")),
    });
    vi.stubGlobal("fetch", m.fn);
    const store = memoryStore();
    const c = controller(store);
    await c.init();
    expect(c.get().phase).toBe("ready");
    const seen: unknown[] = [];
    c.on("message", (d) => seen.push(d));
    await c.send("How long is the X200 warranty?");
    const [user, answer] = c.get().messages;
    expect(user).toMatchObject({ role: "user", id: "msg_fixture_u1" });
    expect(answer).toMatchObject({ id: "msg_fixture_a1", status: "done", finish: "stop", text: "The X200 warranty lasts **24 months** [1]." });
    expect(answer!.citations[0]).toMatchObject({ n: 1, title: "X200 Manual", page: 3 });
    expect(c.client.conversationId).toBe("conv_fixture1");
    expect(seen).toHaveLength(2);
    const post = m.calls.find((x) => x.method === "POST")!;
    expect(post.headers.authorization).toBe("Bearer pk_tenant_a_000000000000");
    expect(post.headers["x-codura-visitor"]).toBeTruthy();
    expect(post.headers["accept-language"]).toBe("en");
    expect(post.body).toEqual({ question: "How long is the X200 warranty?", conversation_id: null });
  });

  it("restores history for the same key, and a second Tenant's key on the same device sees none", async () => {
    const m = mockFetch({
      "GET /api/v1/channel/config": () => json(CONFIG),
      "GET /api/v1/conversations/*": () =>
        json({ items: [
          { id: "m1", role: "user", content: "warranty?", language: "en", citations: [], feedback: null, finish_reason: null, created_at: "" },
          { id: "m2", role: "assistant", content: "24 months [1]", language: "en", citations: [{ n: 1, document_id: "d", title: "Manual", page: 1, heading_path: [], snippet: "x" }], feedback: 1, finish_reason: "stop", created_at: "" },
        ] }),
    });
    vi.stubGlobal("fetch", m.fn);
    const store = memoryStore();
    const a = controller(store);
    a.client.conversationId = "conv_a";
    await a.init();
    expect(a.get().messages.map((x) => x.id)).toEqual(["m1", "m2"]);
    expect(a.get().messages[1]!.feedback).toBe(1);

    const b = controller(store, "pk_tenant_b_111111111111");
    await b.init();
    expect(b.get().messages).toEqual([]);
    expect(b.client.visitorId).not.toBe(a.client.visitorId);
    expect(m.calls.filter((x) => x.url.includes("/conversations/")).length).toBe(1); // B never asked for A's
  });

  it("forgets a Conversation the server no longer knows (404)", async () => {
    vi.stubGlobal("fetch", mockFetch({ "GET /api/v1/channel/config": () => json(CONFIG) }).fn);
    const c = controller();
    c.client.conversationId = "conv_gone";
    await c.init();
    expect(c.client.conversationId).toBeFalsy();
    expect(c.get().phase).toBe("ready");
  });

  it("shows the pre-chat form, validates, and sends the email with the first message only", async () => {
    const posted: unknown[] = [];
    vi.stubGlobal("fetch", mockFetch({
      "GET /api/v1/channel/config": () => json({ ...CONFIG, visitor_email: { mode: "required", consent_text: "OK to contact me", privacy_url: "https://acme.example/p" } }),
      "POST /api/v1/chat/stream": (call) => (posted.push(call.body), sse(fixture("answer.txt"))),
    }).fn);
    const c = controller();
    await c.init();
    expect(c.get().phase).toBe("prechat");
    expect(c.submitPrechat(null, false)).toBe("prechat_invalid");
    expect(c.submitPrechat("not-an-email", true)).toBe("prechat_invalid");
    expect(c.submitPrechat("lead@client.example", false)).toBe("prechat_consent_required");
    expect(c.submitPrechat("lead@client.example", true)).toBe("ok");
    expect(c.get().phase).toBe("ready");
    await c.send("first?");
    await c.send("second?");
    expect(posted[0]).toMatchObject({ visitor_email: "lead@client.example", consent: true });
    expect(posted[1]).not.toHaveProperty("visitor_email");
  });

  it("optional email can be skipped", async () => {
    vi.stubGlobal("fetch", mockFetch({ "GET /api/v1/channel/config": () => json({ ...CONFIG, visitor_email: { mode: "optional", consent_text: "", privacy_url: null } }) }).fn);
    const c = controller();
    await c.init();
    expect(c.submitPrechat(null, false)).toBe("ok");
    expect(c.get().phase).toBe("ready");
  });

  it("stop aborts the stream and keeps the partial answer as cancelled", async () => {
    const first = 'event: meta\ndata: {"conversation_id":"c1","message_id":"a1","user_message_id":"u1","language":"en"}\n\nevent: delta\ndata: {"text":"Partial"}\n\n';
    vi.stubGlobal("fetch", mockFetch({
      "GET /api/v1/channel/config": () => json(CONFIG),
      "POST /api/v1/chat/stream": (_c, init) => hangingSse(first, init.signal),
    }).fn);
    const c = controller();
    await c.init();
    const sending = c.send("long question");
    await vi.waitFor(() => expect(c.get().messages[1]?.text).toBe("Partial"));
    expect(c.get().streaming).toBe(true);
    c.stop();
    await sending;
    expect(c.get().messages[1]).toMatchObject({ status: "cancelled", text: "Partial" });
    expect(c.get().streaming).toBe(false);
  });

  it("marks errors, retries, and handles 429 rate limits", async () => {
    let n = 0;
    vi.stubGlobal("fetch", mockFetch({
      "GET /api/v1/channel/config": () => json(CONFIG),
      "POST /api/v1/chat/stream": () => {
        n += 1;
        if (n === 1) return sse(fixture("error.txt"));
        if (n === 2) return json({ error: { code: "rate_limited", message: "slow down" } }, 429);
        return sse(fixture("fallback.txt"));
      },
    }).fn);
    const c = controller();
    await c.init();
    await c.send("q1");
    const failed = c.get().messages[1]!;
    expect(failed).toMatchObject({ status: "error", error: "error" });
    await c.retry(failed.id);
    expect(c.get().messages).toHaveLength(2);
    expect(c.get().messages[1]).toMatchObject({ status: "error", error: "rate_limited" });
    await c.retry(c.get().messages[1]!.id);
    expect(c.get().messages[1]).toMatchObject({ status: "done", finish: "fallback", citations: [] });
  });

  it("sends feedback for persisted answers and reverts on failure", async () => {
    let ok = true;
    const m = mockFetch({
      "GET /api/v1/channel/config": () => json(CONFIG),
      "POST /api/v1/chat/stream": () => sse(fixture("answer.txt")),
      "POST /api/v1/messages/*": () => (ok ? new Response(null, { status: 204 }) : json({ error: { code: "x" } }, 500)),
    });
    vi.stubGlobal("fetch", m.fn);
    const c = controller();
    await c.init();
    await c.send("q");
    await c.feedback("msg_fixture_a1", -1);
    expect(c.get().messages[1]!.feedback).toBe(-1);
    expect(m.calls.at(-1)).toMatchObject({ method: "POST", body: { value: -1 } });
    ok = false;
    await c.feedback("msg_fixture_a1", 1);
    expect(c.get().messages[1]!.feedback).toBe(-1);
  });

  it("refuses to send while offline and recovers", async () => {
    vi.stubGlobal("fetch", mockFetch({ "GET /api/v1/channel/config": () => json(CONFIG) }).fn);
    const c = controller();
    await c.init();
    c.setOffline(true);
    await c.send("hello");
    expect(c.get().messages).toEqual([]);
    expect(c.get().notice).toBe("offline");
    c.setOffline(false);
    expect(c.get().notice).toBeNull();
  });

  it("switches UI language and re-fetches localized config", async () => {
    const m = mockFetch({ "GET /api/v1/channel/config": (call) => json({ ...CONFIG, assistant: { ...CONFIG.assistant, welcome_text: call.headers["accept-language"] === "hi" ? "नमस्ते" : "Hi" } }) });
    vi.stubGlobal("fetch", m.fn);
    const c = controller();
    await c.init();
    await c.setLanguage("hi-IN");
    await tick();
    expect(c.get().lang).toBe("hi");
    expect(c.get().config!.assistant.welcome_text).toBe("नमस्ते");
  });

  it("shows unavailable for an invalid key", async () => {
    vi.stubGlobal("fetch", mockFetch({ "GET /api/v1/channel/config": () => json({ error: { code: "unauthenticated", message: "x" } }, 401) }).fn);
    const c = controller();
    await c.init();
    expect(c.get().phase).toBe("unavailable");
  });
});
