import { describe, expect, it } from "vitest";
import { ChannelClient, SseParser, apiPath, readChatEvents, type KeyValueStore } from "./index";

describe("apiPath", () => {
  it("builds versioned public API paths", () => {
    expect(apiPath("/chat/stream")).toBe("/api/v1/chat/stream");
  });
});

describe("SseParser", () => {
  it("handles chunk boundaries, pings, multi-line data and CRLF", () => {
    const p = new SseParser();
    expect(p.push("event: delta\nda")).toEqual([]);
    expect(p.push('ta: {"text":"Hel"}\n\n: ping\n\n')).toEqual([{ event: "delta", data: '{"text":"Hel"}' }]);
    expect(p.push("event: x\r\ndata: a\r\ndata: b\r\n\r\n")).toEqual([{ event: "x", data: "a\nb" }]);
  });

  it("decodes multi-byte UTF-8 split across chunks", () => {
    const bytes = new TextEncoder().encode('event: delta\ndata: {"text":"वारंटी"}\n\n');
    const p = new SseParser();
    const first = p.push(bytes.slice(0, 30));
    const second = p.push(bytes.slice(30));
    expect([...first, ...second]).toEqual([{ event: "delta", data: '{"text":"वारंटी"}' }]);
  });
});

describe("readChatEvents", () => {
  it("yields typed events and ignores unknown ones", async () => {
    const body = [
      'event: meta\ndata: {"conversation_id":"conv_1","message_id":"m2","user_message_id":"m1","language":"hi"}\n\n',
      'event: future_event\ndata: {"x":1}\n\n',
      'event: delta\ndata: {"text":"24 महीने"}\n\n',
      'event: citations\ndata: {"items":[{"n":1,"title":"Manual","page":3}]}\n\n',
      'event: done\ndata: {"finish_reason":"stop","latency_ms":900}\n\n',
    ].join("");
    const stream = new Response(body).body!;
    const events = [];
    for await (const e of readChatEvents(stream)) events.push(e);
    expect(events.map((e) => e.type)).toEqual(["meta", "delta", "citations", "done"]);
    expect(events[1]).toEqual({ type: "delta", text: "24 महीने" });
  });
});

describe("ChannelClient storage", () => {
  it("keeps visitor ids per Channel Key so two Tenants never share history", () => {
    const data = new Map<string, string>();
    const storage: KeyValueStore = { get: (k) => data.get(k) ?? null, set: (k, v) => void data.set(k, v) };
    const a = new ChannelClient({ apiUrl: "http://x", channelKey: "pk_aaaaaaaaaaaaaaaaaaaa", storage });
    const b = new ChannelClient({ apiUrl: "http://x", channelKey: "pk_bbbbbbbbbbbbbbbbbbbb", storage });
    a.conversationId = "conv_a";
    expect(a.visitorId).toBe(a.visitorId);
    expect(a.visitorId).not.toBe(b.visitorId);
    expect(b.conversationId).toBeNull();
  });
});
