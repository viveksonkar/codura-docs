/**
 * Platform Console, M3 (Platform Admins only):
 * - Plans: versioned editor with a live margin calculator; below the floor needs "loss leader" (M3-02)
 * - Cost rates: pricing inputs incl. the INR/USD rate (FR-70)
 * - Model Profiles: create/edit/test/default, write-only credentials, Custom LLM per Tenant (M3-10/11)
 * - Tenant billing: assign Plan, deal overrides, Entitlements (custom_llm), events, invoices
 * - Invoices: manual collection (mark paid / failed / void) (M3-05)
 * - Finance: MRR, revenue vs COGS by Plan/Model/meter, unprofitable Tenants, simulator, funnel (M3-09)
 */
import { useEffect, useMemo, useState } from "react";
import { api, patch, post } from "../../api";
import { Link } from "../../router";
import { Alert, StatusBadge, errorText, useData, when } from "../../ui";
import { type Currency, METER_LABEL, fromMicros, fromMinor, toMicros, usd } from "../billing/money";

type Msg = { kind: "ok" | "error"; text: string } | null;
interface Price { base_micros: number; overage_micros: Record<string, number> }
interface Margin {
  ok: boolean; floor_usd_micros: number; expected_cogs_micros: number; fixed_cost_micros: number; model_profile_id: string;
  unit_cogs_micros: Record<string, number>;
  currencies: Record<string, { price_usd_micros: number; margin_pct: number | null; base_ok: boolean; ok: boolean; overage: Record<string, { price_usd_micros: number; floor_usd_micros: number; ok: boolean }> }>;
}
interface Plan {
  id: string; code: string; name: string; description: string; is_public: boolean; is_free: boolean; status: string;
  version: number; included: Record<string, number>; entitlements: Record<string, unknown>; overage_enabled: boolean;
  default_model_profile_id: string | null; target_margin_pct: number; loss_leader: boolean; subscribers: number;
  prices: Record<string, Price>; margin: Margin;
}
interface Registry {
  entitlements: Record<string, { kind: string; default: unknown; label: string }>;
  included: Record<string, { kind: string; default: number }>;
  channels: string[];
  currencies: Currency[];
}
interface Profile {
  id: string; tenant_id: string | null; tenant_name: string | null; provider: string; model: string; base_url: string | null;
  license: string; is_local: boolean; status: string; is_default: boolean; has_credentials: boolean; egress: boolean;
  price_in_per_mtok_micros: number; price_out_per_mtok_micros: number; params: Record<string, unknown>;
  plans: string[]; override_tenants: string[];
}

function useAct(reload: () => Promise<void>) {
  const [msg, setMsg] = useState<Msg>(null);
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); setMsg({ kind: "ok", text: ok }); await reload(); } catch (e) { setMsg({ kind: "error", text: errorText(e) }); }
  };
  return { msg, setMsg, act };
}

// --- Plans ----------------------------------------------------------------------------------------

interface Draft {
  code: string; name: string; included: Record<string, number>; entitlements: Record<string, unknown>; overage_enabled: boolean;
  default_model_profile_id: string; target_margin_pct: number; loss_leader: boolean; prices: Record<string, Price>;
}

function toDraft(p: Plan | null, reg: Registry): Draft {
  const blankPrice: Price = { base_micros: 0, overage_micros: {} };
  return {
    code: p?.code ?? "",
    name: p?.name ?? "",
    included: { ...Object.fromEntries(Object.entries(reg.included).map(([k, v]) => [k, v.default])), ...(p?.included ?? {}) },
    entitlements: { ...Object.fromEntries(Object.entries(reg.entitlements).map(([k, v]) => [k, v.default])), ...(p?.entitlements ?? {}) },
    overage_enabled: p?.overage_enabled ?? false,
    default_model_profile_id: p?.default_model_profile_id ?? "",
    target_margin_pct: Number(p?.target_margin_pct ?? 70),
    loss_leader: p?.loss_leader ?? false,
    prices: Object.fromEntries(reg.currencies.map((c) => [c, p?.prices[c] ?? blankPrice])),
  };
}

function body(d: Draft) {
  return {
    included: d.included, entitlements: d.entitlements, overage_enabled: d.overage_enabled,
    default_model_profile_id: d.default_model_profile_id || null, target_margin_pct: d.target_margin_pct,
    loss_leader: d.loss_leader, prices: d.prices,
  };
}

export function PlatformPlans() {
  const plans = useData<{ items: Plan[]; registry: Registry }>("/api/platform/plans");
  const profiles = useData<{ items: Profile[] }>("/api/platform/model-profiles");
  const { msg, act } = useAct(plans.reload);
  const [editing, setEditing] = useState<string | null>(null);
  const reg = plans.data?.registry;
  return (
    <>
      <h1>Plans</h1>
      <p className="muted">Versioned: a version that a subscription has used can't change, so saving creates a new version. Subscribers move at their next period, or now with “Migrate”. Prices below the margin floor are refused unless marked loss leader.</p>
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
      <table>
        <thead><tr><th>Plan</th><th>USD</th><th>INR</th><th>Included answers</th><th>Margin</th><th>Subscribers</th><th /></tr></thead>
        <tbody>{plans.data?.items.map((p) => (
          <tr key={p.id}>
            <td>{p.name} <span className="muted small">v{p.version} · {p.code}{p.is_public ? "" : " · hidden"}</span>{p.status !== "active" && <StatusBadge status={p.status} />}</td>
            <td>{p.prices.USD ? fromMicros(p.prices.USD.base_micros, "USD") : "—"}</td>
            <td>{p.prices.INR ? fromMicros(p.prices.INR.base_micros, "INR") : "—"}</td>
            <td>{p.included.answer?.toLocaleString()}</td>
            <td>{p.margin.ok ? <span className="badge badge-active">above floor</span> : <span className={p.loss_leader ? "badge" : "badge badge-failed"}>{p.loss_leader ? "loss leader" : "below floor"}</span>}
              <div className="small muted">floor {usd(p.margin.floor_usd_micros)}</div></td>
            <td>{p.subscribers}</td>
            <td className="actions">
              <button className="link" onClick={() => setEditing(p.id)}>Edit</button>
              <button className="link" onClick={() => void act(() => post(`/api/platform/plans/${p.id}/migrate`), "Subscribers moved to the current version")}>Migrate</button>
              <button className="link" onClick={() => void act(() => patch(`/api/platform/plans/${p.id}`, { is_public: !p.is_public }), p.is_public ? "Hidden from Tenants" : "Shown to Tenants")}>{p.is_public ? "Hide" : "Show"}</button>
            </td>
          </tr>))}</tbody>
      </table>
      <button onClick={() => setEditing("new")}>New plan</button>
      {editing && reg && (
        <PlanEditor
          key={editing}
          plan={plans.data?.items.find((p) => p.id === editing) ?? null}
          registry={reg}
          profiles={(profiles.data?.items ?? []).filter((p) => !p.tenant_id && p.status === "active")}
          onClose={() => setEditing(null)}
          onSaved={async () => { setEditing(null); await plans.reload(); }}
        />
      )}
    </>
  );
}

function PlanEditor({ plan, registry, profiles, onClose, onSaved }: {
  plan: Plan | null; registry: Registry; profiles: Profile[]; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const [d, setD] = useState<Draft>(() => toDraft(plan, registry));
  const [margin, setMargin] = useState<Margin | null>(plan?.margin ?? null);
  const [error, setError] = useState<string | null>(null);
  const payload = useMemo(() => body(d), [d]);

  useEffect(() => {
    const t = window.setTimeout(async () => {
      try { setMargin(await post<Margin>("/api/platform/plans/margin", payload)); setError(null); } catch (e) { setError(errorText(e)); }
    }, 300);
    return () => window.clearTimeout(t);
  }, [payload]);

  const setPrice = (cur: string, field: string, micros: number) => setD((x) => {
    const p = x.prices[cur] ?? { base_micros: 0, overage_micros: {} };
    const next = field === "base" ? { ...p, base_micros: micros } : { ...p, overage_micros: { ...p.overage_micros, [field]: micros } };
    return { ...x, prices: { ...x.prices, [cur]: next } };
  });

  const save = async () => {
    try {
      if (plan) await post(`/api/platform/plans/${plan.id}/versions`, payload);
      else await post("/api/platform/plans", { ...payload, code: d.code, name: d.name });
      await onSaved();
    } catch (e) { setError(errorText(e)); }
  };

  return (
    <div className="card form">
      <div className="inline spread"><h3>{plan ? `Edit ${plan.name} (creates v${plan.version + 1})` : "New plan"}</h3><button className="link" onClick={onClose}>Close</button></div>
      {!plan && (
        <div className="inline">
          <label>Code<input value={d.code} onChange={(e) => setD({ ...d, code: e.target.value })} /></label>
          <label>Name<input value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} /></label>
        </div>
      )}
      <div className="split">
        <div>
          <h4>Prices</h4>
          <table>
            <thead><tr><th /> {registry.currencies.map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>
              {["base", "answer", "page_ocr", "answer_custom_llm"].map((field) => (
                <tr key={field}>
                  <th>{field === "base" ? "Monthly fee" : `Overage / ${METER_LABEL[field] ?? field}`}</th>
                  {registry.currencies.map((c) => {
                    const p = d.prices[c];
                    const micros = field === "base" ? p?.base_micros ?? 0 : p?.overage_micros[field] ?? 0;
                    return <td key={c}><input inputMode="decimal" defaultValue={micros / 1_000_000} onChange={(e) => setPrice(c, field, toMicros(e.target.value))} /></td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <h4>Included</h4>
          {Object.entries(registry.included).map(([k, spec]) => (
            <label key={k} className="inline">{METER_LABEL[k] ?? k} <span className="muted small">({spec.kind})</span>
              <input type="number" min={0} value={d.included[k] ?? 0} onChange={(e) => setD({ ...d, included: { ...d.included, [k]: Number(e.target.value) } })} />
            </label>
          ))}
        </div>
        <div>
          <h4>Entitlements</h4>
          {Object.entries(registry.entitlements).map(([k, spec]) => (
            <EntitlementInput key={k} name={k} spec={spec} channels={registry.channels} value={d.entitlements[k]}
              onChange={(v) => setD({ ...d, entitlements: { ...d.entitlements, [k]: v } })} />
          ))}
          <label className="check"><input type="checkbox" checked={d.overage_enabled} onChange={(e) => setD({ ...d, overage_enabled: e.target.checked })} /> Overage allowed (otherwise hard stop)</label>
          <label>Default Model Profile
            <select value={d.default_model_profile_id} onChange={(e) => setD({ ...d, default_model_profile_id: e.target.value })}>
              <option value="">Platform default</option>
              {profiles.map((p) => <option key={p.id} value={p.id}>{p.id}{p.egress ? " (remote)" : ""}</option>)}
            </select>
          </label>
          <label className="inline">Target margin %<input type="number" min={0} max={99} value={d.target_margin_pct} onChange={(e) => setD({ ...d, target_margin_pct: Number(e.target.value) })} /></label>
          <label className="check"><input type="checkbox" checked={d.loss_leader} onChange={(e) => setD({ ...d, loss_leader: e.target.checked })} /> Loss leader (allowed below the floor; audited)</label>
        </div>
      </div>
      {margin && (
        <div className={`alert ${margin.ok ? "alert-ok" : d.loss_leader ? "alert-info" : "alert-error"}`}>
          <strong>{margin.ok ? "Above the margin floor" : "Below the margin floor"}</strong> · floor {usd(margin.floor_usd_micros)} / month
          (expected COGS {usd(margin.expected_cogs_micros)} + fixed {usd(margin.fixed_cost_micros)}, model {margin.model_profile_id || "default"})
          <ul className="small">{Object.entries(margin.currencies).map(([c, m]) => (
            <li key={c}>{c}: {usd(m.price_usd_micros)} → margin {m.margin_pct ?? "—"}% {m.base_ok ? "✓" : "✗"}
              {Object.entries(m.overage).filter(([, o]) => !o.ok).map(([meter, o]) => <span key={meter} className="error"> · {meter} overage below {usd(o.floor_usd_micros)}</span>)}
            </li>))}</ul>
        </div>
      )}
      {error && <Alert>{error}</Alert>}
      <button onClick={() => void save()}>{plan ? "Save as new version" : "Create plan"}</button>
    </div>
  );
}

function EntitlementInput({ name, spec, value, channels, onChange }: {
  name: string; spec: { kind: string; label: string }; value: unknown; channels: string[]; onChange: (v: unknown) => void;
}) {
  if (spec.kind === "bool") return <label className="check"><input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} /> {spec.label}</label>;
  if (spec.kind === "int") return <label className="inline">{spec.label}<input type="number" min={0} value={Number(value ?? 0)} onChange={(e) => onChange(Number(e.target.value))} /></label>;
  if (spec.kind === "enum") return (
    <label className="inline">{spec.label}
      <select value={String(value)} onChange={(e) => onChange(e.target.value)}>{["off", "optional", "required"].map((v) => <option key={v}>{v}</option>)}</select>
    </label>
  );
  const list = (value as string[] | undefined) ?? [];
  return (
    <fieldset><legend>{spec.label}</legend>{channels.map((c) => (
      <label key={`${name}-${c}`} className="check"><input type="checkbox" checked={list.includes(c)}
        onChange={(e) => onChange(e.target.checked ? [...list, c] : list.filter((x) => x !== c))} /> {c}</label>
    ))}</fieldset>
  );
}

// --- Cost rates -----------------------------------------------------------------------------------

export function PlatformCostRates() {
  const rates = useData<{ items: { key: string; micros: number; unit: string; updated_at: string }[] }>("/api/platform/cost-rates");
  const { msg, act } = useAct(rates.reload);
  const [edits, setEdits] = useState<Record<string, number>>({});
  return (
    <>
      <h1>Cost rates</h1>
      <p className="muted">COGS inputs and pricing assumptions. Snapshots are stored on every Usage Event, so changes affect new usage only. <code>fx_usd_inr</code> converts INR revenue to USD for margins.</p>
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
      <table>
        <thead><tr><th>Key</th><th>Value (micros or count)</th><th>Unit</th><th>Updated</th></tr></thead>
        <tbody>{rates.data?.items.map((r) => (
          <tr key={r.key}><td><code>{r.key}</code></td>
            <td><input type="number" min={0} defaultValue={r.micros} onChange={(e) => setEdits({ ...edits, [r.key]: Number(e.target.value) })} /></td>
            <td className="small">{r.unit}</td><td className="small">{when(r.updated_at)}</td></tr>))}</tbody>
      </table>
      <button disabled={!Object.keys(edits).length} onClick={() => void act(() => api("PUT", "/api/platform/cost-rates", { rates: edits }), "Rates saved")}>Save changes</button>
    </>
  );
}

// --- Model Profiles -----------------------------------------------------------------------------

const EMPTY_PROFILE = { id: "", tenant_id: "", provider: "openai_compat", model: "", base_url: "", license: "commercial-api", api_key: "", price_in: "0", price_out: "0", effort: "" };

export function PlatformModelProfiles() {
  const profiles = useData<{ items: Profile[] }>("/api/platform/model-profiles");
  const { msg, setMsg, act } = useAct(profiles.reload);
  const [form, setForm] = useState({ ...EMPTY_PROFILE });
  const [tests, setTests] = useState<Record<string, string>>({});
  return (
    <>
      <h1>Model Profiles</h1>
      <p className="muted">Platform-managed answering models (ADR-0019). Self-hosted models must be Apache-2.0 or MIT (ADR-0018). Credentials are write-only. A Tenant-scoped profile is that Tenant's Custom LLM and answers only while the Tenant has the <code>custom_llm</code> Entitlement.</p>
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
      <table>
        <thead><tr><th>Profile</th><th>Provider / model</th><th>Licence</th><th>Data</th><th>Price in/out per 1M</th><th>Used by</th><th /></tr></thead>
        <tbody>{profiles.data?.items.map((p) => (
          <tr key={p.id}>
            <td>{p.id}{p.is_default && <span className="badge badge-ready">default</span>}<StatusBadge status={p.status} />
              {p.tenant_id && <div className="small">Custom LLM of {p.tenant_name}</div>}</td>
            <td>{p.provider}<div><code className="small">{p.model}</code></div>{p.base_url && <div className="small muted">{p.base_url}</div>}</td>
            <td>{p.license}</td>
            <td>{p.egress ? <span className="warn">leaves this server</span> : "local"}<div className="small">{p.has_credentials ? "credentials set" : "no credentials"}</div></td>
            <td>{usd(p.price_in_per_mtok_micros)} / {usd(p.price_out_per_mtok_micros)}</td>
            <td className="small">{[...p.plans.map((x) => `plan ${x}`), ...p.override_tenants.map((x) => `override: ${x}`)].join(", ") || "—"}</td>
            <td className="actions">
              <button className="link" onClick={async () => {
                setTests({ ...tests, [p.id]: "testing…" });
                try {
                  const r = await post<{ ok: boolean; latency_ms: number; sample?: string; message?: string }>(`/api/platform/model-profiles/${p.id}/test`);
                  setTests((t) => ({ ...t, [p.id]: r.ok ? `ok in ${r.latency_ms} ms: “${r.sample}”` : `failed: ${r.message}` }));
                } catch (e) { setTests((t) => ({ ...t, [p.id]: errorText(e) })); }
              }}>Test</button>
              {!p.tenant_id && !p.is_default && p.status === "active" && <button className="link" onClick={() => void act(() => post(`/api/platform/model-profiles/${p.id}/default`), `${p.id} is now the default`)}>Make default</button>}
              <button className="link" onClick={() => void act(() => patch(`/api/platform/model-profiles/${p.id}`, { status: p.status === "active" ? "disabled" : "active" }), "Status changed")}>{p.status === "active" ? "Disable" : "Enable"}</button>
              <button className="link" onClick={() => {
                const key = prompt(`New API key for ${p.id} (stored encrypted, never shown again). Empty clears it.`);
                if (key === null) return;
                void act(() => patch(`/api/platform/model-profiles/${p.id}`, key ? { api_key: key } : { clear_credentials: true }), "Credentials updated");
              }}>Credentials</button>
              {tests[p.id] && <div className="small">{tests[p.id]}</div>}
            </td>
          </tr>))}</tbody>
      </table>
      <form className="card form" onSubmit={async (e) => {
        e.preventDefault();
        try {
          await post("/api/platform/model-profiles", {
            id: form.id, tenant_id: form.tenant_id || null, provider: form.provider, model: form.model,
            base_url: form.base_url || null, license: form.license, api_key: form.api_key || null,
            price_in_per_mtok_micros: toMicros(form.price_in), price_out_per_mtok_micros: toMicros(form.price_out),
            params: form.effort ? { effort: form.effort } : {},
          });
          setForm({ ...EMPTY_PROFILE });
          setMsg({ kind: "ok", text: "Profile created" });
          await profiles.reload();
        } catch (x) { setMsg({ kind: "error", text: errorText(x) }); }
      }}>
        <h3>New profile</h3>
        <div className="inline">
          <label>Id<input required value={form.id} onChange={(e) => setForm({ ...form, id: e.target.value })} placeholder="premium" /></label>
          <label>Provider<select value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value, license: e.target.value === "anthropic" ? "commercial-api" : form.license })}>
            <option value="openai_compat">openai_compat</option><option value="anthropic">anthropic</option><option value="ollama">ollama</option></select></label>
          <label>Model<input required value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder={form.provider === "anthropic" ? "claude-opus-5-5" : "Qwen/Qwen3-14B"} /></label>
          <label>Licence<select value={form.license} onChange={(e) => setForm({ ...form, license: e.target.value })}>
            <option>commercial-api</option><option>apache-2.0</option><option>mit</option></select></label>
        </div>
        <div className="inline">
          <label>Base URL<input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} placeholder="https://… (optional for anthropic)" /></label>
          <label>API key<input type="password" autoComplete="off" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} /></label>
          <label>Price in $/1M<input inputMode="decimal" value={form.price_in} onChange={(e) => setForm({ ...form, price_in: e.target.value })} /></label>
          <label>Price out $/1M<input inputMode="decimal" value={form.price_out} onChange={(e) => setForm({ ...form, price_out: e.target.value })} /></label>
          {form.provider === "anthropic" && <label>Effort<select value={form.effort} onChange={(e) => setForm({ ...form, effort: e.target.value })}>
            <option value="">low (default)</option><option>medium</option><option>high</option></select></label>}
        </div>
        <label>Custom LLM for Tenant id (optional)<input value={form.tenant_id} onChange={(e) => setForm({ ...form, tenant_id: e.target.value })} placeholder="tnt_…" /></label>
        <button>Create profile</button>
      </form>
    </>
  );
}

// --- Tenant billing -----------------------------------------------------------------------------

interface TenantBilling {
  terms: { plan: { code: string; name: string }; status: string; included: Record<string, number>; entitlements: Record<string, unknown>; overage_enabled: boolean; currency: string | null };
  account: { country: string | null; currency: string | null; billing_email: string | null; company_name: string | null } | null;
  override: { included: Record<string, number>; entitlements: Record<string, unknown>; overage_enabled: boolean | null; note: string } | null;
  events: { action: string; actor: string; from_status: string | null; to_status: string | null; created_at: string; details: unknown }[];
  invoices: { id: string; number: string; kind: string; currency: Currency; status: string; total_minor: number; issued_at: string | null }[];
  custom_llm_profiles: { id: string; provider: string; model: string; status: string }[];
}

export function PlatformTenantBilling({ tenantId }: { tenantId: string }) {
  const view = useData<TenantBilling>(`/api/platform/tenants/${tenantId}/billing`);
  const plans = useData<{ items: Plan[] }>("/api/platform/plans");
  const { msg, act } = useAct(view.reload);
  const [included, setIncluded] = useState("");
  const [note, setNote] = useState("");
  const v = view.data;
  useEffect(() => {
    if (v) { setIncluded(JSON.stringify(v.override?.included ?? {})); setNote(v.override?.note ?? ""); }
  }, [v]);
  if (!v) return <><h1>Tenant billing</h1>{view.error && <Alert>{view.error}</Alert>}</>;
  const put = (b: unknown, ok: string) => act(() => api("PUT", `/api/platform/tenants/${tenantId}/billing`, b), ok);
  const ents = v.override?.entitlements ?? {};
  return (
    <>
      <p><Link to="/platform/tenants">← Tenants</Link></p>
      <h1>Billing · {v.account?.company_name ?? tenantId}</h1>
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
      <div className="cards">
        <div className="card stat"><div className="muted small">Plan</div><div className="big">{v.terms.plan.name}</div><StatusBadge status={v.terms.status} /></div>
        <div className="card stat"><div className="muted small">Billing</div><div>{v.account?.country ?? "no country"} · {v.terms.currency ?? "—"}</div><div className="small">{v.account?.billing_email}</div></div>
      </div>
      <div className="card form">
        <h3>Assign plan (immediate, no proration)</h3>
        <div className="inline">
          {plans.data?.items.map((p) => <button key={p.code} className="secondary" onClick={() => void put({ plan_code: p.code }, `Moved to ${p.name}`)}>{p.name}</button>)}
        </div>
        <h3>Deal overrides</h3>
        <label className="check"><input type="checkbox" checked={Boolean(ents.custom_llm)} onChange={(e) => void put({ entitlements: { ...ents, custom_llm: e.target.checked } }, e.target.checked ? "Custom LLM enabled" : "Custom LLM disabled")} /> Custom LLM (answers via the Tenant's own profile, metered as answer_custom_llm)</label>
        <label className="check"><input type="checkbox" checked={Boolean(ents.remove_branding)} onChange={(e) => void put({ entitlements: { ...ents, remove_branding: e.target.checked } }, "Saved")} /> Remove branding</label>
        <label>Included quantities override (JSON, e.g. {`{"answer": 50000}`})<input value={included} onChange={(e) => setIncluded(e.target.value)} /></label>
        <label>Note<input value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} /></label>
        <div className="inline">
          <button onClick={() => { try { void put({ included: JSON.parse(included || "{}"), note }, "Overrides saved"); } catch { alert("Included quantities must be JSON"); } }}>Save overrides</button>
          <button className="secondary" onClick={() => void put({ overage_enabled: !v.terms.overage_enabled }, "Overage toggled")}>{v.terms.overage_enabled ? "Disable overage" : "Enable overage"}</button>
        </div>
        {v.custom_llm_profiles.length > 0
          ? <p className="small">Custom LLM profiles: {v.custom_llm_profiles.map((p) => `${p.id} (${p.provider} ${p.model}, ${p.status})`).join(", ")}</p>
          : <p className="small muted">No Custom LLM profile yet: create one under <Link to="/platform/models">Model Profiles</Link> with this Tenant id ({tenantId}).</p>}
      </div>
      <h2>Invoices</h2>
      <InvoiceTable rows={v.invoices.map((i) => ({ ...i, tenant_name: "" }))} onChange={view.reload} />
      <h2>Subscription events</h2>
      <table><thead><tr><th>When</th><th>Action</th><th>Status</th><th>Actor</th></tr></thead>
        <tbody>{v.events.map((e, i) => <tr key={i}><td>{when(e.created_at)}</td><td>{e.action}</td><td>{e.from_status ?? ""}{e.to_status ? ` → ${e.to_status}` : ""}</td><td className="small">{e.actor}</td></tr>)}</tbody></table>
    </>
  );
}

// --- Invoices -----------------------------------------------------------------------------------

interface InvoiceRow { id: string; number: string; kind: string; currency: Currency; status: string; total_minor: number; issued_at: string | null; tenant_name?: string; tenant_id?: string }

function InvoiceTable({ rows, onChange }: { rows: InvoiceRow[]; onChange: () => Promise<void> }) {
  const { msg, act } = useAct(onChange);
  return (
    <>
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
      <table>
        <thead><tr><th>Number</th><th>Tenant</th><th>Type</th><th>Total</th><th>Status</th><th>Issued</th><th /></tr></thead>
        <tbody>{rows.map((i) => (
          <tr key={i.id}>
            <td>{i.number}</td><td>{i.tenant_id ? <Link to={`/platform/tenants/${i.tenant_id}/billing`}>{i.tenant_name}</Link> : i.tenant_name}</td><td>{i.kind}</td>
            <td>{fromMinor(i.total_minor, i.currency)}</td><td><StatusBadge status={i.status} /></td><td>{when(i.issued_at)}</td>
            <td className="actions">{i.status === "open" && <>
              <button className="link" onClick={() => {
                const method = prompt("Payment method (bank_transfer, upi, card, cash…)", "bank_transfer");
                if (method) void act(() => post(`/api/platform/invoices/${i.id}/mark-paid`, { method }), "Marked paid");
              }}>Mark paid</button>
              <button className="link" onClick={() => void act(() => post(`/api/platform/invoices/${i.id}/payment-failed`), "Payment failure recorded (grace period starts)")}>Payment failed</button>
              <button className="link danger" onClick={() => confirm("Void this invoice?") && void act(() => post(`/api/platform/invoices/${i.id}/void`), "Voided")}>Void</button>
            </>}</td>
          </tr>))}</tbody>
      </table>
    </>
  );
}

export function PlatformInvoices() {
  const [status, setStatus] = useState("open");
  const inv = useData<{ items: InvoiceRow[] }>(`/api/platform/invoices${status ? `?status=${status}` : ""}`, [status]);
  return (
    <>
      <h1>Invoices</h1>
      <p className="muted">With the <code>manual</code> provider, record payments here. With Stripe, webhooks update payment status.</p>
      <select value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">All</option><option value="open">Open</option><option value="paid">Paid</option><option value="void">Void</option><option value="uncollectible">Uncollectible</option>
      </select>
      <InvoiceTable rows={inv.data?.items ?? []} onChange={inv.reload} />
    </>
  );
}

// --- Finance ------------------------------------------------------------------------------------

interface Summary {
  mrr_usd_micros: number; arr_usd_micros: number; paying_tenants: number; revenue_usd_micros: number; cogs_usd_micros: number;
  gross_margin_pct: number | null; contribution_margin_pct: number | null; fixed_cost_usd_micros: number;
  by_plan: { plan: string; revenue: number; cogs: number; margin_pct: number | null }[];
  by_model: { model_profile_id: string; answers: number; revenue: number; cogs: number; margin_pct: number | null }[];
  by_meter: { meter: string; cogs: number }[];
}
interface TenantMargin { tenant_id: string; name: string; plan: string | null; revenue_usd_micros: number; cogs_usd_micros: number; margin_pct: number | null; floor_pct: number; unprofitable: boolean; free_cost: boolean; causes: string[] }

export function PlatformFinance() {
  const [days, setDays] = useState(30);
  const s = useData<Summary>(`/api/platform/finance/summary?days=${days}`, [days]);
  const t = useData<{ items: TenantMargin[]; unprofitable: TenantMargin[] }>(`/api/platform/finance/tenants?days=${days}`, [days]);
  const f = useData<{ signups: number; verified: number; activated: number; paid: number; conversion_pct: Record<string, number | null> }>("/api/platform/finance/funnel");
  const [sim, setSim] = useState({ plan_code: "growth", usd: "", inr: "", answers: "" });
  const [simOut, setSimOut] = useState<string | null>(null);
  const d = s.data;
  const pct = (v: number | null) => (v === null ? "—" : `${v}%`);
  return (
    <>
      <div className="inline spread"><h1>Finance</h1>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 30, 90, 365].map((n) => <option key={n} value={n}>Last {n} days</option>)}</select></div>
      <p className="muted">All amounts in USD (INR converted with <code>fx_usd_inr</code>). Revenue = issued invoices; COGS = metered usage.</p>
      {d && (
        <div className="cards">
          <div className="card stat"><div className="muted small">MRR</div><div className="big">{usd(d.mrr_usd_micros)}</div><div className="small">ARR {usd(d.arr_usd_micros)} · {d.paying_tenants} paying</div></div>
          <div className="card stat"><div className="muted small">Revenue</div><div className="big">{usd(d.revenue_usd_micros)}</div></div>
          <div className="card stat"><div className="muted small">COGS</div><div className="big">{usd(d.cogs_usd_micros)}</div></div>
          <div className="card stat"><div className="muted small">Gross / contribution margin</div><div className="big">{pct(d.gross_margin_pct)}</div><div className="small">{pct(d.contribution_margin_pct)} after {usd(d.fixed_cost_usd_micros)} fixed</div></div>
        </div>
      )}
      {t.data && t.data.unprofitable.length > 0 && (
        <Alert>{t.data.unprofitable.length} Tenant(s) below their plan's margin floor: {t.data.unprofitable.map((x) => x.name).join(", ")}</Alert>
      )}
      <div className="split">
        <div>
          <h2>By plan</h2>
          <table><thead><tr><th>Plan</th><th>Revenue</th><th>COGS</th><th>Margin</th></tr></thead>
            <tbody>{d?.by_plan.map((r) => <tr key={r.plan}><td>{r.plan}</td><td>{usd(r.revenue)}</td><td>{usd(r.cogs)}</td><td>{pct(r.margin_pct)}</td></tr>)}</tbody></table>
          <h2>By model</h2>
          <table><thead><tr><th>Profile</th><th>Answers</th><th>Revenue</th><th>COGS</th><th>Margin</th></tr></thead>
            <tbody>{d?.by_model.map((r) => <tr key={r.model_profile_id}><td>{r.model_profile_id}</td><td>{r.answers.toLocaleString()}</td><td>{usd(r.revenue)}</td><td>{usd(r.cogs)}</td><td>{pct(r.margin_pct)}</td></tr>)}</tbody></table>
          <h2>COGS by meter</h2>
          <table><tbody>{d?.by_meter.map((r) => <tr key={r.meter}><td>{METER_LABEL[r.meter] ?? r.meter}</td><td>{usd(r.cogs)}</td></tr>)}</tbody></table>
        </div>
        <div>
          <h2>Funnel (90 days)</h2>
          {f.data && <table><tbody>{(["signups", "verified", "activated", "paid"] as const).map((k) => (
            <tr key={k}><td>{k}</td><td>{f.data![k]}</td><td>{f.data!.conversion_pct[k] ?? "—"}%</td></tr>))}</tbody></table>}
          <h2>Price simulator</h2>
          <form className="card form" onSubmit={async (e) => {
            e.preventDefault();
            const base: Record<string, number> = {};
            if (sim.usd) base.USD = toMicros(sim.usd);
            if (sim.inr) base.INR = toMicros(sim.inr);
            try {
              const r = await post<{ tenants: number; revenue_usd_micros: number; cogs_usd_micros: number; margin_pct: number | null }>("/api/platform/finance/simulate", {
                plan_code: sim.plan_code, base_micros: base, included_answers: sim.answers ? Number(sim.answers) : null,
              });
              setSimOut(`${r.tenants} Tenant(s): revenue ${usd(r.revenue_usd_micros)}, COGS ${usd(r.cogs_usd_micros)}, margin ${pct(r.margin_pct)} (last 30 days of usage)`);
            } catch (x) { setSimOut(errorText(x)); }
          }}>
            <label>Plan code<input value={sim.plan_code} onChange={(e) => setSim({ ...sim, plan_code: e.target.value })} /></label>
            <div className="inline">
              <label>Monthly USD<input inputMode="decimal" value={sim.usd} onChange={(e) => setSim({ ...sim, usd: e.target.value })} /></label>
              <label>Monthly INR<input inputMode="decimal" value={sim.inr} onChange={(e) => setSim({ ...sim, inr: e.target.value })} /></label>
              <label>Included answers<input type="number" value={sim.answers} onChange={(e) => setSim({ ...sim, answers: e.target.value })} /></label>
            </div>
            <button>Simulate</button>
            {simOut && <p className="small">{simOut}</p>}
          </form>
        </div>
      </div>
      <h2>Tenants</h2>
      <table><thead><tr><th>Tenant</th><th>Plan</th><th>Revenue</th><th>COGS</th><th>Margin</th><th>Flag</th></tr></thead>
        <tbody>{t.data?.items.map((r) => (
          <tr key={r.tenant_id} className={r.unprofitable ? "flagged" : ""}>
            <td><Link to={`/platform/tenants/${r.tenant_id}/billing`}>{r.name}</Link></td><td>{r.plan ?? "—"}</td>
            <td>{usd(r.revenue_usd_micros)}</td><td>{usd(r.cogs_usd_micros)}</td><td>{pct(r.margin_pct)} <span className="muted small">floor {r.floor_pct}%</span></td>
            <td>{r.unprofitable ? <span className="error">unprofitable{r.causes.length ? `: ${r.causes.join(", ")}` : ""}</span> : r.free_cost ? <span className="muted">free plan cost</span> : ""}</td>
          </tr>))}</tbody></table>
    </>
  );
}
