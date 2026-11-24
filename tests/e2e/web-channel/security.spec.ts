/**
 * Against the compose stack (M2-09, SEC-05/07/15): Origin rejection, cross-Tenant ids, and XSS
 * payloads in answers and Citations rendered by the real widget.js.
 */
import { expect, test } from "@playwright/test";
import { Admin, DEMO, readState } from "../lib/api";

const visitor = () => `e2e-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

test("widget keys are refused from a non-allowed Origin; CORS reflects only allowed ones", async ({ request }) => {
  const h = (origin: string) => ({ Authorization: `Bearer ${DEMO.widgetKey}`, "X-Codura-Visitor": visitor(), Origin: origin });
  const evil = await request.get("/api/v1/channel/config", { headers: h("https://evil.example") });
  expect(evil.status()).toBe(403);
  expect((await evil.json()).error.code).toBe("origin_not_allowed");
  expect(evil.headers()["access-control-allow-origin"]).toBeUndefined();
  const ok = await request.get("/api/v1/channel/config", { headers: h("http://localhost:8080") });
  expect(ok.status()).toBe(200);
  expect(ok.headers()["access-control-allow-origin"]).toBe("http://localhost:8080");
  const hosted = await request.get("/api/v1/hosted/demo/help");
  const hostedKey = (await hosted.json()).channel_key as string;
  const hostedElsewhere = await request.get("/api/v1/channel/config", {
    headers: { Authorization: `Bearer ${hostedKey}`, "X-Codura-Visitor": visitor(), Origin: "https://evil.example" },
  });
  expect(hostedElsewhere.status()).toBe(403);
});

test("another Tenant's key cannot read or rate the demo Tenant's Conversations", async ({ request }) => {
  const admin = await Admin.login(DEMO.admin, DEMO.password);
  const list = await admin.call<{ items: { id: string }[] }>("GET", "/api/admin/conversations?limit=1");
  test.skip(list.items.length === 0, "needs one demo Conversation (run parity first)");
  const conv = await admin.call<{ id: string; messages: { id: string; role: string }[] }>("GET", `/api/admin/conversations/${list.items[0]!.id}`);
  const answer = conv.messages.find((m) => m.role === "assistant")!;
  const other = readState().other;
  const h = { Authorization: `Bearer ${other.key}`, "X-Codura-Visitor": visitor() };
  expect((await request.get(`/api/v1/conversations/${conv.id}/messages`, { headers: h })).status()).toBe(404);
  expect((await request.post(`/api/v1/messages/${answer.id}/feedback`, { headers: h, data: { value: -1 } })).status()).toBe(404);
  const stream = await request.post("/api/v1/chat/stream", { headers: h, data: { question: "hi", conversation_id: conv.id } });
  expect(stream.status()).toBe(404);
  const otherAdmin = await Admin.login(other.email, other.password);
  await expect(otherAdmin.call("GET", `/api/admin/conversations/${conv.id}`)).rejects.toThrow(/404/);
});

const XSS_STREAM = [
  'event: meta\ndata: {"conversation_id":"conv_xss","message_id":"msg_xss_a","user_message_id":"msg_xss_u","language":"en"}\n\n',
  `event: delta\ndata: ${JSON.stringify({ text: '<img src=x onerror="window.__pwned=1"> <script>window.__pwned=2</script> [click](javascript:window.__pwned=3) <a href="javascript:alert(1)">a</a> **bold**' })}\n\n`,
  `event: citations\ndata: ${JSON.stringify({ items: [{ n: 1, document_id: "d", title: '<img src=x onerror="window.__pwned=4">', page: 1, heading_path: ["<svg onload=alert(1)>"], snippet: "<script>window.__pwned=5</script>" }] })}\n\n`,
  'event: done\ndata: {"finish_reason":"stop","latency_ms":3}\n\n',
].join("");

test("XSS payloads in answers and Citations are neutralised by the widget (SEC-07)", async ({ page }) => {
  const dialogs: string[] = [];
  page.on("dialog", (d) => {
    dialogs.push(d.message());
    void d.dismiss();
  });
  await page.route("**/api/v1/chat/stream", (route) =>
    route.fulfill({ status: 200, headers: { "Content-Type": "text/event-stream" }, body: XSS_STREAM }),
  );
  await page.route("**/api/v1/conversations/**", (route) => route.fulfill({ status: 404, body: "{}" }));
  await page.goto(`/demo/?key=${DEMO.widgetKey}&lang=en`);
  const chat = page.locator("#inline codura-chat");
  await expect(chat.locator(".prechat, .composer").first()).toBeVisible();
  const skip = chat.locator(".prechat button.link");
  if (await skip.isVisible()) await skip.click();
  await chat.locator("textarea").fill("show me");
  await chat.locator("button.send").click();
  const answer = chat.locator(".row.assistant:not(.welcome)").last();
  await expect(answer.locator("strong")).toHaveText("bold");
  await answer.locator(".chip").click();
  await expect(answer.locator(".pop")).toContainText("<script>window.__pwned=5</script>");
  await expect(answer.locator("img, script, svg")).toHaveCount(0);
  for (const href of await answer.locator("a").evaluateAll((as) => as.map((a) => a.getAttribute("href")))) {
    expect(href ?? "").not.toMatch(/^javascript:/i);
  }
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
  expect(dialogs).toEqual([]);
});
