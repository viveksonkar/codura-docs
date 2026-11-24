/**
 * Billing UI e2e (M3-13): a Trial Tenant in India upgrades to Starter in INR through the
 * `manual` provider, the Platform Admin marks the invoice paid, and the platform pages (Plans with
 * the margin check, Finance, Invoices) render. Runs against the compose stack.
 */
import { expect, test } from "@playwright/test";
import { readState } from "../lib/api";

test("Trial → Starter in INR, invoice paid by the Platform Admin", async ({ page }) => {
  const { other } = readState();
  await page.goto("/login");
  await page.getByLabel("Email").fill(other.email);
  await page.getByLabel("Password").fill(other.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("link", { name: "Billing" }).click();
  await expect(page.getByRole("heading", { name: "Billing" })).toBeVisible();
  await expect(page.locator(".badge", { hasText: "trialing" })).toBeVisible();

  await page.getByLabel("Country").selectOption("IN");
  await expect(page.getByText("Billed in INR.", { exact: true })).toBeVisible();
  await page.getByLabel("Company name").fill("Other E2E Pvt Ltd");
  await page.getByRole("button", { name: "Save billing details" }).click();
  await expect(page.getByText("Billing details saved")).toBeVisible();
  await expect(page.locator(".card.plan", { hasText: "Starter" })).toContainText("₹1,999");

  await page.getByRole("button", { name: "Choose Starter" }).click();
  await expect(page.getByText("Plan updated.")).toBeVisible();
  await expect(page.locator(".card.plan.current")).toContainText("Starter");
  const row = page.locator("table tbody tr", { hasText: "proration" }).first();
  await expect(row).toContainText("₹");
  await expect(row).toContainText("open");

  // Platform Admin records the payment.
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.getByLabel("Email").fill("platform@local.test");
  await page.getByLabel("Password").fill("admin");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("link", { name: "Invoices" }).click();
  const invoice = page.locator("table tbody tr", { hasText: "Other E2E" }).first();
  page.once("dialog", (d) => void d.accept("upi"));
  await invoice.getByRole("button", { name: "Mark paid" }).click();
  await expect(page.getByText("Marked paid")).toBeVisible();

  await page.getByRole("link", { name: "Plans" }).click();
  await expect(page.locator("table tbody tr", { hasText: "Growth" })).toContainText("above floor");
  await page.getByRole("button", { name: "New plan" }).click();
  await expect(page.getByText(/margin floor/).first()).toBeVisible();

  await page.getByRole("link", { name: "Finance" }).click();
  await expect(page.getByText("MRR")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Price simulator" })).toBeVisible();
});

test("Demo Tenant on Growth: no branding on the widget config", async ({ request }) => {
  const res = await request.get("/api/v1/channel/config", {
    headers: {
      Authorization: "Bearer pk_demo_widget_local_only_00000000000000000000000",
      "X-Codura-Visitor": "e2e-billing-visitor",
      Origin: "http://localhost:8080",
    },
  });
  expect(res.status()).toBe(200);
  expect((await res.json()).branding).toBe(false);
});
