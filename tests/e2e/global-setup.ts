/**
 * Seed what the browser tests need on the running stack, idempotently:
 * - the demo Tenant's Knowledge Base gets the X200 manual (ingested by the worker with real bge-m3);
 * - a second, freshly signed-up Tenant with its own widget key (the "second Tenant on the same
 *   browser" check, channels.md §6).
 */
import { Admin, DEMO, signupVerified, waitFor, writeState } from "./lib/api";

const MANUAL = `# X200 Smart Thermostat Manual

## Warranty
The X200 warranty lasts 24 months from the date of purchase. Keep your receipt as proof of purchase.

## Resetting the X200
To reset the X200, hold the power button for ten seconds until the screen turns blue.

## Returns
Returns are accepted within 30 days of delivery when the device is in its original packaging.
`;

export default async function globalSetup(): Promise<void> {
  const ready = await fetch(`${process.env.CODURA_E2E_BASE_URL ?? "http://localhost:8080"}/readyz`).catch(() => null);
  if (!ready?.ok) throw new Error("The compose stack is not ready: run `docker compose up -d` first");

  const demo = await Admin.login(DEMO.admin, DEMO.password);
  const assistants = (await demo.call<{ items: any[] }>("GET", "/api/admin/assistants")).items;
  const help = assistants.find((a) => a.slug === "help");
  if (!help) throw new Error("seeded demo Assistant 'help' missing (SEED_DEMO_TENANT=true?)");
  const kbId: string = help.knowledge_base_ids[0];
  const docs = (await demo.call<{ items: any[] }>("GET", `/api/admin/knowledge-bases/${kbId}/documents`)).items;
  if (!docs.some((d) => d.filename === "x200-manual.md" && d.status === "ready")) {
    if (!docs.some((d) => d.filename === "x200-manual.md")) {
      const form = new FormData();
      form.append("file", new Blob([MANUAL], { type: "text/markdown" }), "x200-manual.md");
      await demo.call("POST", `/api/admin/knowledge-bases/${kbId}/documents`, form);
    }
    await waitFor(async () => {
      const items = (await demo.call<{ items: any[] }>("GET", `/api/admin/knowledge-bases/${kbId}/documents`)).items;
      const d = items.find((x) => x.filename === "x200-manual.md");
      if (d?.status === "failed") throw new Error(`ingestion failed: ${d.error}`);
      return d?.status === "ready";
    }, "the demo manual to be ingested");
  }
  // Keep the demo Assistant in its seeded shape: optional email, hosted page on.
  await demo.call("PATCH", `/api/admin/assistants/${help.id}`, { visitor_email: "optional", hosted_enabled: true });

  const run = Date.now().toString(36);
  const email = `owner-${run}@other-e2e.test`;
  const password = "e2e-password-123";
  const other = await signupVerified(`Other E2E ${run}`, email, password);
  const kb = await other.call("POST", "/api/admin/knowledge-bases", { name: "Other docs" });
  const asst = await other.call("POST", "/api/admin/assistants", { name: "Other Help", knowledge_base_ids: [kb.id] });
  const key = await other.call("POST", "/api/admin/channel-keys", {
    channel: "widget",
    assistant_id: asst.id,
    allowed_origins: ["http://localhost:8080"],
  });
  writeState({ demoAssistantId: help.id, demoKbId: kbId, other: { key: key.key, email, password } });
}
