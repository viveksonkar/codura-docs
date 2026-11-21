import { useState } from "react";
import { patch, post } from "../api";
import { Alert, StatusBadge, errorText, useData, when } from "../ui";

interface TenantRow {
  id: string; slug: string; name: string; status: string; plan_code: string; model_profile_override: string | null;
  members: number; documents: number; answers_this_month: number; cogs_micros_this_month: number; created_at: string;
}
interface Profile { id: string; provider: string; model: string; license: string; is_local: boolean; status: string; is_default: boolean; tenant_id: string | null; price_in_per_mtok_micros: number; price_out_per_mtok_micros: number }

export function PlatformTenants() {
  const [q, setQ] = useState("");
  const tenants = useData<{ items: TenantRow[] }>(`/api/platform/tenants${q ? `?q=${encodeURIComponent(q)}` : ""}`, [q]);
  const profiles = useData<{ items: Profile[] }>("/api/platform/model-profiles");
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); setMsg({ kind: "ok", text: ok }); void tenants.reload(); } catch (e) { setMsg({ kind: "error", text: errorText(e) }); }
  };
  return (
    <>
      <h1>Tenants</h1>
      <p className="muted">Platform-wide view. The answering model is chosen here (override) or by the default profile; Tenants never see models or costs.</p>
      <input placeholder="Search name or slug" value={q} onChange={(e) => setQ(e.target.value)} />
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
      <table>
        <thead><tr><th>Organisation</th><th>Status</th><th>Users</th><th>Docs</th><th>Answers (month)</th><th>COGS (month)</th><th>Model override</th><th /></tr></thead>
        <tbody>{tenants.data?.items.map((t) => (
          <tr key={t.id}>
            <td>{t.name}<div className="muted small">{t.slug} · since {when(t.created_at)}</div></td>
            <td><StatusBadge status={t.status} /></td>
            <td>{t.members}</td><td>{t.documents}</td><td>{t.answers_this_month}</td>
            <td>${(t.cogs_micros_this_month / 1_000_000).toFixed(4)}</td>
            <td><select value={t.model_profile_override ?? ""} onChange={(e) => void act(() =>
              patch(`/api/platform/tenants/${t.id}`, e.target.value ? { model_profile_override: e.target.value } : { clear_model_override: true }), "Model updated")}>
              <option value="">Default</option>
              {profiles.data?.items.filter((p) => !p.tenant_id && p.status === "active").map((p) => <option key={p.id} value={p.id}>{p.id}</option>)}
            </select></td>
            <td>{t.status === "suspended"
              ? <button className="link" onClick={() => void act(() => patch(`/api/platform/tenants/${t.id}`, { status: "active" }), "Reactivated")}>Reactivate</button>
              : <button className="link danger" onClick={() => confirm(`Suspend ${t.name}? Their chat and console stop working.`) && void act(() => patch(`/api/platform/tenants/${t.id}`, { status: "suspended" }), "Suspended")}>Suspend</button>}</td>
          </tr>))}</tbody>
      </table>
    </>
  );
}

export function PlatformModels() {
  const { data } = useData<{ items: Profile[] }>("/api/platform/model-profiles");
  return (
    <>
      <h1>Model Profiles</h1>
      <p className="muted">Platform-managed answering models (ADR-0019). Editing, prices and Custom LLM arrive with monetisation (M3). Self-hosted models must be Apache-2.0 or MIT.</p>
      <table>
        <thead><tr><th>Profile</th><th>Provider</th><th>Model</th><th>Licence</th><th>Where</th><th>Price in/out per 1M tokens</th><th /></tr></thead>
        <tbody>{data?.items.map((p) => (
          <tr key={p.id}><td>{p.id}{p.is_default && <span className="badge badge-ready">default</span>}</td><td>{p.provider}</td><td><code>{p.model}</code></td>
            <td>{p.license}</td><td>{p.is_local ? "local" : <span className="warn">data leaves this server</span>}</td>
            <td>${(p.price_in_per_mtok_micros / 1e6).toFixed(2)} / ${(p.price_out_per_mtok_micros / 1e6).toFixed(2)}</td><td><StatusBadge status={p.status} /></td></tr>))}</tbody>
      </table>
    </>
  );
}

export function PlatformAudit() {
  const { data } = useData<{ items: { id: string; actor: string; action: string; target_tenant_id: string | null; details: unknown; created_at: string }[] }>("/api/platform/audit");
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState("");
  return (
    <>
      <h1>Platform admins & audit log</h1>
      <form className="inline" onSubmit={async (e) => {
        e.preventDefault();
        try { await post("/api/platform/admins/invites", { email }); setMsg(`Invitation sent to ${email}`); setEmail(""); } catch (x) { setMsg(errorText(x)); }
      }}>
        <input type="email" required placeholder="new.platform.admin@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
        <button>Invite Platform Admin</button>
      </form>
      {msg && <Alert kind="info">{msg}</Alert>}
      <table>
        <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Tenant</th><th>Details</th></tr></thead>
        <tbody>{data?.items.map((a) => (
          <tr key={a.id}><td>{when(a.created_at)}</td><td>{a.actor}</td><td>{a.action}</td><td className="small">{a.target_tenant_id ?? "—"}</td><td className="small"><code>{JSON.stringify(a.details)}</code></td></tr>))}</tbody>
      </table>
    </>
  );
}
