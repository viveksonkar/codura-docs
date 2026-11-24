/**
 * Web Channel parity checklist (channels.md §6, FR-45, M2-09) on the Hosted Chat Page and on the
 * Widget demo page, with real local models:
 *   welcome in the device locale → cited answer → second-language question answered in that
 *   language → follow-up using history → fallback → stop mid-stream → feedback → reload keeps the
 *   Conversation → a second Tenant's key on the same browser shows none of the first's history.
 */
import { type Locator, type Page, expect, test } from "@playwright/test";
import { Admin, DEMO, readState } from "../lib/api";

const HINDI_WELCOME = "नमस्ते! डेमो दस्तावेज़ों के बारे में कुछ भी पूछिए।";
const FALLBACK_EN = "Sorry, I couldn't find that in our documents";

interface Target {
  name: "hosted" | "widget";
  url: (key?: string) => string;
  chat: (page: Page) => Locator;
}

const TARGETS: Target[] = [
  { name: "hosted", url: () => "/c/demo/help", chat: (p) => p.locator("#codura-hosted codura-chat") },
  {
    name: "widget",
    url: (key = DEMO.widgetKey) => `/demo/?key=${encodeURIComponent(key)}`,
    chat: (p) => p.locator("#inline codura-chat"),
  },
];

/** Wait until the widget has loaded; skip the optional pre-chat email form if it is shown. */
async function skipPrechat(chat: Locator): Promise<void> {
  await expect(chat.locator(".prechat, .composer").first()).toBeVisible();
  const skip = chat.locator(".prechat button.link");
  if (await skip.isVisible()) await skip.click();
  await expect(chat.locator(".composer")).toBeVisible();
}

/** Ask and wait until the answer is finished (Send is back). Returns the answer bubble. */
async function ask(chat: Locator, question: string): Promise<Locator> {
  const before = await chat.locator(".row.assistant:not(.welcome)").count();
  await chat.locator("textarea").fill(question);
  await chat.locator("button.send").click();
  const answer = chat.locator(".row.assistant:not(.welcome)").nth(before);
  await expect(answer.locator(".md")).toBeVisible();
  await expect(chat.locator("button.send", { hasText: /send|भेजें/i })).toBeVisible();
  await expect(answer.locator(".cursor")).toHaveCount(0);
  return answer;
}

async function latestConversation(admin: Admin, channel: string): Promise<{ id: string; messages: any[] }> {
  const list = await admin.call<{ items: { id: string }[] }>("GET", `/api/admin/conversations?channel=${channel}&limit=1`);
  return admin.call("GET", `/api/admin/conversations/${list.items[0]!.id}`);
}

for (const target of TARGETS) {
  test.describe(`${target.name} parity`, () => {
    test(`${target.name}: full checklist`, async ({ browser }) => {
      const admin = await Admin.login(DEMO.admin, DEMO.password);
      const context = await browser.newContext({ locale: "hi-IN" });
      const page = await context.newPage();
      const dialogs: string[] = [];
      page.on("dialog", (d) => {
        dialogs.push(d.message());
        void d.dismiss();
      });

      // 1. Welcome text in the device locale (Hindi), UI strings in Hindi.
      await page.goto(target.url());
      const chat = target.chat(page);
      await skipPrechat(chat);
      await expect(chat.locator(".welcome .bubble")).toHaveText(HINDI_WELCOME);
      await expect(chat.locator("button.send")).toHaveText("भेजें");
      await expect(chat.locator(".foot")).toContainText("Codura");

      // 2. Cited answer.
      const first = await ask(chat, "How long is the X200 warranty?");
      await expect(first).toContainText("24");
      await expect(first.locator(".chip").first()).toBeVisible();
      await first.locator(".chip").first().click();
      await expect(first.locator(".pop")).toContainText("X200");

      // 3. A question in a second language is answered in that language.
      const es = await ask(chat, "¿Cuánto tiempo tengo para devolver el X200?");
      await expect(es).toContainText("30");

      // 4. Follow-up that needs the history.
      const follow = await ask(chat, "And how do I reset it?");
      await expect(follow).toContainText(/ten|10/i);

      // 5. Fallback on an unrelated question: configured text, no Citations.
      const fallback = await ask(chat, "What is the capital city of Mongolia?");
      await expect(fallback).toContainText(FALLBACK_EN);
      await expect(fallback.locator(".chip")).toHaveCount(0);

      // Languages as detected by the server, per message.
      const conv = await latestConversation(admin, target.name);
      const langs = conv.messages.filter((m) => m.role === "assistant").map((m) => m.language);
      expect(langs.slice(0, 3)).toEqual(["en", "es", "en"]);

      // 6. Stop mid-stream: the UI returns to idle with no error, the partial answer stays.
      await chat.locator("textarea").fill("Explain everything about the X200 warranty, reset and returns in detail.");
      await chat.locator("button.send").click();
      const stop = chat.locator("button.send", { hasText: "रोकें" });
      if (await stop.isVisible({ timeout: 5_000 }).catch(() => false)) await stop.click();
      await expect(chat.locator("button.send", { hasText: "भेजें" })).toBeVisible();
      await expect(chat.locator('.err[role="alert"]')).toHaveCount(0);

      // 7. Feedback, recorded on the server.
      await first.locator('.fb button[aria-label="उपयोगी"]').click();
      await expect(first.locator('.fb button[aria-label="उपयोगी"]')).toHaveAttribute("aria-pressed", "true");
      await expect
        .poll(async () => (await latestConversation(admin, target.name)).messages.some((m) => m.feedback === 1))
        .toBe(true);

      // 8. Reload keeps the Conversation.
      const userTurns = await chat.locator(".row.user").count();
      await page.reload();
      await expect(target.chat(page).locator(".row.user")).toHaveCount(userTurns);
      await expect(target.chat(page).locator(".row.assistant:not(.welcome)").first()).toContainText("24");

      // 9. Another Tenant's key in the same browser sees none of it.
      const other = readState().other;
      await page.goto(TARGETS[1]!.url(other.key));
      const otherChat = TARGETS[1]!.chat(page);
      await expect(otherChat.locator(".welcome")).toBeVisible();
      await expect(otherChat.locator(".row.user")).toHaveCount(0);
      await expect(otherChat).not.toContainText("X200");

      expect(dialogs).toEqual([]);
      await context.close();
    });
  });
}

test("hosted page: ?embed=1 hides the header, ?lang overrides the locale, unknown slug is a 404 page", async ({ page }) => {
  await page.goto("/c/demo/help?embed=1&lang=ar");
  const chat = page.locator("#codura-hosted codura-chat");
  await skipPrechat(chat);
  await expect(chat.locator(".panel")).toHaveAttribute("dir", "rtl");
  await expect(chat.locator("header.head")).toHaveCount(0);
  await expect(chat.locator("button.send")).toHaveText("إرسال");
  await expect(page).toHaveTitle("Demo Help");
  await page.goto("/c/demo/no-such-assistant");
  await expect(page.locator(".missing")).toHaveText("This chat page does not exist.");
});
