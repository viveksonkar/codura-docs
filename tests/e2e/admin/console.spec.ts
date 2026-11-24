/**
 * Admin Console UI e2e (M1-14) plus the M2 pages: sign in → Knowledge Base with an ingested
 * Document → Assistant Playground answer with Citations → Channels (hosted link + QR, new widget
 * key + snippet) → Conversations → Insights. Real models; demo Tenant.
 */
import { expect, test } from "@playwright/test";
import { DEMO } from "../lib/api";

test("Tenant Admin: documents, playground, channels, conversations, insights", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(DEMO.admin);
  await page.getByLabel("Password").fill(DEMO.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  await page.getByRole("link", { name: "Knowledge Bases" }).click();
  await page.getByRole("link", { name: "Demo documents" }).click();
  await expect(page.getByText("x200-manual.md")).toBeVisible();

  await page.getByRole("link", { name: "Assistants & Playground" }).click();
  await page.getByRole("link", { name: "Demo Help" }).click();
  await page.getByPlaceholder("Ask a question…").fill("How long is the X200 warranty?");
  await page.getByRole("button", { name: "Send" }).click();
  const answer = page.locator(".playground .msg-assistant").last();
  await expect(answer.locator(".md")).toContainText("24");
  await expect(answer.locator(".cite").first()).toContainText("X200");

  await page.getByRole("link", { name: "Channels & keys" }).click();
  await expect(page.getByRole("heading", { name: "Hosted Chat Page" })).toBeVisible();
  await expect(page.getByRole("link", { name: /\/c\/demo\/help$/ })).toBeVisible();
  await expect(page.getByRole("img", { name: "QR code for the chat page" })).toBeVisible();
  await page.getByPlaceholder("Label (e.g. Main website)").fill("E2E site");
  const origin = `https://shop-${Date.now().toString(36)}.e2e.example`;
  await page.locator("textarea[placeholder='https://www.example.com']").first().fill(origin);
  await page.getByRole("button", { name: "Create key" }).click();
  await expect(page.locator("pre.snippet").first()).toContainText("data-key=\"pk_");
  await expect(page.getByRole("cell", { name: origin })).toBeVisible();

  await page.getByRole("link", { name: "Conversations" }).click();
  await expect(page.locator("table tbody tr").first()).toBeVisible();
  await page.locator("table tbody tr td a").first().click();
  await expect(page.getByRole("heading", { name: "Conversation" })).toBeVisible();

  await page.getByRole("link", { name: "Insights" }).click();
  await expect(page.getByRole("heading", { name: "Unanswered questions" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Visitor emails" })).toBeVisible();
});

test("Tenant User: read-only, no keys or Visitor emails", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("user@demo.test");
  await page.getByLabel("Password").fill(DEMO.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Channels & keys" })).toHaveCount(0);
  await page.getByRole("link", { name: "Insights" }).click();
  await expect(page.getByRole("heading", { name: "Unanswered questions" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Visitor emails" })).toHaveCount(0);
});
