/**
 * M1-14 through the browser: self-service signup → verify via the Mailpit link → Knowledge Base +
 * upload → Assistant → Playground in two languages with Citations → fallback → invite a read-only
 * Tenant User who accepts through the emailed link. (OCR, DOCX and scanned PDFs are covered with
 * real models by backend/scripts/live_e2e.py, which CI also runs with egress blocked.)
 */
import { expect, test } from "@playwright/test";
import { lastMail } from "../lib/api";

const GUIDE = `# Orbit Kettle Guide

## Boiling
The Orbit kettle boils one litre of water in four minutes.

## Cleaning
Clean the Orbit kettle with white vinegar once a month.
`;

const GUIDE_HTML = `<!doctype html><html><head><title>Orbit Warranty</title></head><body>
<h1>Orbit Warranty</h1><p>The Orbit kettle has a warranty of 18 months.</p></body></html>`;

function linkFrom(text: string, path: string): string {
  const m = new RegExp(`https?://[^\\s]+${path}\\?token=[A-Za-z0-9_\\-]+`).exec(text);
  if (!m) throw new Error(`no ${path} link in email`);
  return new URL(m[0]).pathname + new URL(m[0]).search;
}

test("signup → verify → upload → answer in two languages → invite a Tenant User", async ({ page, browser }) => {
  const run = Date.now().toString(36);
  const email = `founder-${run}@orbit-e2e.test`;
  const password = "orbit-password-1";

  await page.goto("/signup");
  await page.getByLabel("Organisation name").fill(`Orbit ${run}`);
  await page.getByLabel("Your name").fill("Founder");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password (10+ characters)").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.locator(".alert-ok")).toBeVisible();

  await page.goto(linkFrom(await lastMail(email), "/verify-email"));
  await page.getByRole("button", { name: "Verify email" }).click();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByText("Verify your email")).toHaveCount(0);

  await page.getByRole("link", { name: "Knowledge Bases" }).click();
  await page.getByPlaceholder("New Knowledge Base name").fill("Orbit docs");
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("link", { name: "Orbit docs" }).click();
  await page.locator("input[type=file]").setInputFiles([
    { name: "orbit-guide.md", mimeType: "text/markdown", buffer: Buffer.from(GUIDE) },
    { name: "orbit-warranty.html", mimeType: "text/html", buffer: Buffer.from(GUIDE_HTML) },
  ]);
  await expect(page.locator(".badge-ready")).toHaveCount(2, { timeout: 180_000 });

  await page.getByRole("link", { name: "Assistants & Playground" }).click();
  await page.getByPlaceholder("New assistant name").fill("Orbit Help");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("heading", { name: "Orbit Help" })).toBeVisible(); // opens the new Assistant
  const input = page.getByPlaceholder("Ask a question…");
  const answers = page.locator(".playground .msg-assistant");

  await input.fill("How long does the Orbit kettle take to boil a litre?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(answers.nth(0).locator(".md")).toContainText(/four|4/i);
  await expect(answers.nth(0).locator(".cite").first()).toContainText("Orbit");
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();

  await input.fill("Orbit केतली की वारंटी कितने महीने की है?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(answers.nth(1).locator(".md")).toContainText("18");
  await expect(answers.nth(1).locator(".md")).toContainText(/[ऀ-ॿ]/); // answered in Hindi
  await expect(answers.nth(1)).toContainText("Hindi");
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();

  await input.fill("Who won the football world cup in 1998?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(answers.nth(2)).toContainText("fallback");
  await expect(answers.nth(2).locator(".cite")).toHaveCount(0);

  await page.getByRole("link", { name: "Overview" }).click();
  await expect(page.locator(".card.stat").first()).toContainText("2");

  // Invite a read-only Tenant User.
  const viewerEmail = `viewer-${run}@orbit-e2e.test`;
  await page.getByRole("link", { name: "Users" }).click();
  await page.getByPlaceholder("email@company.com").fill(viewerEmail);
  await page.getByRole("button", { name: "Invite" }).click();
  const viewer = await browser.newContext();
  const vp = await viewer.newPage();
  await vp.goto(linkFrom(await lastMail(viewerEmail), "/accept-invite"));
  await vp.getByLabel("Your name").fill("Viewer");
  await vp.getByLabel("Password (new, or your existing one)").fill("viewer-password-1");
  await vp.getByRole("button", { name: "Continue" }).click();
  await expect(vp.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(vp.getByText("read-only access")).toBeVisible();
  await expect(vp.getByRole("link", { name: "Channels & keys" })).toHaveCount(0);
  await vp.getByRole("link", { name: "Knowledge Bases" }).click();
  await expect(vp.getByPlaceholder("New Knowledge Base name")).toHaveCount(0);
  await viewer.close();
});
