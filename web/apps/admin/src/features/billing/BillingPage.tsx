/**
 * Tenant billing (M3-07, FR-68; specs/billing.md §9): Plan, usage vs quota with projection,
 * plans in the Tenant's currency (INR for India, USD elsewhere), upgrade/downgrade, cancel,
 * spend cap, billing details, invoices and the payment-method portal. Tenant Users read only.
 * Quantities and prices only: the API never returns COGS or model names.
 */
import { useEffect, useState } from "react";
import { api, post } from "../../api";
import { Alert, ReadOnlyNote, StatusBadge, errorText, useData, when } from "../../ui";
import { COUNTRIES, type Currency, METER_LABEL, currencyFor, fromMicros, fromMinor, toMicros } from "./money";

interface Meter { meter: string; kind: "flow" | "stock"; used: number; limit: number | null; projected?: number }
interface Invoice { id: string; number: string; kind: string; period_start: string; currency: Currency; status: string; total_minor: number; issued_at: string | null; due_at: string | null; hosted_url: string | null }
interface Overview {
  plan: { code: string; name: string; is_free: boolean };
  status: "trialing" | "active" | "past_due" | "none";
  trial_ends_at: string | null;
  trial_plan: string | null;
  entitlements: Record<string, unknown>;
  overage_enabled: boolean;
  currency: Currency | null;
  spend_cap_micros: number | null;
  pending_plan: string | null;
  cancel_at_period_end: boolean;
  period_start: string;
  meters: Meter[];
  notices: { meter: string; threshold: number }[];
  overage_spend_micros: number;
  account: { country: string | null; currency: Currency | null; billing_email: string | null; company_name: string | null; tax_id: string | null; currency_locked: boolean } | null;
  invoices: Invoice[];
  provider: { name: string; portal: boolean };
}
interface PlanCard {
  code: string; name: string; description: string; is_free: boolean; current: boolean; overage_enabled: boolean;
  included: Record<string, number>; entitlements: Record<string, unknown>;
  prices: Record<string, { base_micros: number; overage_micros: Record<string, number> }>;
}

const FEATURE_LABEL: Record<string, (v: unknown) => string | null> = {
  channels: (v) => `Channels: ${(v as string[]).join(", ")}`,
  remove_branding: (v) => (v ? "No “Powered by” branding" : null),
  visitor_email: (v) => (v === "off" ? null : `Visitor email: ${String(v)}`),
  analytics_retention_days: (v) => `${String(v)} days of analytics`,
  custom_llm: (v) => (v ? "Custom LLM (on request)" : null),
};

export function BillingPage({ canEdit }: { canEdit: boolean }) {
  const overview = useData<Overview>("/api/admin/billing");
  const plans = useData<{ items: PlanCard[]; currency: Currency | null }>("/api/admin/billing/plans");
  const [msg, setMsg] = useState<{ kind: "ok" | "error" | "info"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const o = overview.data;

  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("checkout");
    if (q === "success") setMsg({ kind: "ok", text: "Payment received. Your plan updates as soon as the payment provider confirms it." });
    if (q === "cancelled") setMsg({ kind: "info", text: "Checkout was cancelled. Nothing was charged." });
  }, []);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try {
      await fn();
      setMsg({ kind: "ok", text: ok });
      await Promise.all([overview.reload(), plans.reload()]);
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  };

  const changePlan = async (code: string) => {
    setBusy(true);
    try {
      const r = await post<{ applied: boolean; kind: string; redirect_url: string | null }>("/api/admin/billing/change-plan", { plan_code: code });
      if (r.redirect_url) {
        window.location.assign(r.redirect_url);
        return;
      }
      setMsg({ kind: "ok", text: r.kind === "downgrade" ? "Your plan changes at the start of next month." : "Plan updated." });
      await Promise.all([overview.reload(), plans.reload()]);
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  };

  if (!o) return <><h1>Billing</h1>{overview.error && <Alert>{overview.error}</Alert>}</>;
  const currency: Currency | null = o.currency ?? o.account?.currency ?? null;
  return (
    <>
      <h1>Billing</h1>
      <ReadOnlyNote canEdit={canEdit} />
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
      {o.status === "past_due" && <Alert>Your subscription is past due. Chat answers are paused and the console is read-only until the open invoice is paid.</Alert>}

      <div className="cards">
        <div className="card stat">
          <div className="muted small">Current plan</div>
          <div className="big">{o.plan.name}</div>
          <StatusBadge status={o.status} />
          {o.status === "trialing" && o.trial_ends_at && (
            <p className="small">Trial with {o.trial_plan} limits until {new Date(o.trial_ends_at).toLocaleDateString()}, then Free.</p>
          )}
          {o.pending_plan && <p className="small warn">Changes to {o.pending_plan} on the 1st of next month.</p>}
          {o.cancel_at_period_end && <p className="small warn">Cancels at the end of this month (then Free).</p>}
        </div>
        <div className="card stat">
          <div className="muted small">Overage this month</div>
          <div className="big">{currency ? fromMicros(o.overage_spend_micros, currency) : "—"}</div>
          <div className="small muted">{o.overage_enabled ? "Usage above your plan is billed per unit." : "Usage stops at your plan limits."}</div>
          {o.spend_cap_micros !== null && currency && <div className="small">Spend cap: {fromMicros(o.spend_cap_micros, currency)}</div>}
        </div>
      </div>

      <h2>Usage this month</h2>
      <table>
        <thead><tr><th>Meter</th><th>Used</th><th>Included</th><th>Projected (month end)</th><th /></tr></thead>
        <tbody>{o.meters.filter((m) => m.limit !== null).map((m) => {
          const pct = m.limit ? Math.min(100, Math.round((m.used / m.limit) * 100)) : 0;
          const over = m.projected !== undefined && m.limit !== null && m.projected > m.limit;
          return (
            <tr key={m.meter}>
              <td>{METER_LABEL[m.meter] ?? m.meter}</td>
              <td>{m.used.toLocaleString()}</td>
              <td>{m.limit?.toLocaleString()}</td>
              <td className={over ? "warn" : ""}>{m.kind === "flow" ? m.projected?.toLocaleString() : "—"}</td>
              <td style={{ width: "28%" }}><progress max={100} value={pct} aria-label={`${pct}% used`} /> <span className="small">{pct}%</span></td>
            </tr>
          );
        })}</tbody>
      </table>

      <h2>Plans</h2>
      {!currency && <p className="muted">Prices are shown in USD and INR. Add your billing country below: India is billed in INR, every other country in USD.</p>}
      <div className="cards">
        {plans.data?.items.map((p) => {
          const shown = Object.entries(p.prices);
          return (
            <div key={p.code} className={`card plan ${p.current ? "current" : ""}`}>
              <h3>{p.name}{p.current && <span className="badge badge-active">current</span>}</h3>
              {shown.map(([cur, price]) => (
                <div key={cur}>
                  <span className="big">{p.is_free ? "Free" : fromMicros(price.base_micros, cur as Currency)}</span>
                  {!p.is_free && <span className="muted"> / month</span>}
                  {p.overage_enabled && price.overage_micros.answer !== undefined && (
                    <div className="small muted">then {fromMicros(price.overage_micros.answer, cur as Currency, true)} per extra answer</div>
                  )}
                </div>
              ))}
              <ul className="small">
                <li>{p.included.answer?.toLocaleString()} answers / month</li>
                <li>{p.included.pages_stored?.toLocaleString()} pages · {p.included.page_ocr?.toLocaleString()} OCR pages / month</li>
                <li>{p.included.seats} users · {p.included.assistants} assistants</li>
                {Object.entries(p.entitlements).map(([k, v]) => {
                  const label = FEATURE_LABEL[k]?.(v);
                  return label ? <li key={k}>{label}</li> : null;
                })}
              </ul>
              {canEdit && !p.current && (
                <button disabled={busy} className={p.is_free ? "secondary" : ""} onClick={() => void changePlan(p.code)}>
                  {p.is_free ? "Switch to Free" : `Choose ${p.name}`}
                </button>
              )}
            </div>
          );
        })}
      </div>

      {canEdit && (
        <div className="split">
          <AccountForm account={o.account} onSave={(body) => act(() => api("PUT", "/api/admin/billing/account", body), "Billing details saved")} busy={busy} />
          <div className="card">
            <h3>Spend & subscription</h3>
            <SpendCap currency={currency} cap={o.spend_cap_micros} busy={busy}
              onSave={(v) => act(() => api("PUT", "/api/admin/billing/spend-cap", { spend_cap_micros: v }), v === null ? "Spend cap removed" : "Spend cap saved")} />
            {!o.plan.is_free && o.status !== "trialing" && (
              o.cancel_at_period_end
                ? <button className="secondary" disabled={busy} onClick={() => void act(() => post("/api/admin/billing/resume"), "Cancellation undone")}>Keep my plan</button>
                : <button className="danger" disabled={busy} onClick={() => confirm("Cancel at the end of this month? You move to Free and keep your data.") && void act(() => post("/api/admin/billing/cancel"), "Your plan ends at the end of this month")}>Cancel plan</button>
            )}
            {o.provider.portal && (
              <p><button className="secondary" disabled={busy} onClick={async () => {
                try { window.location.assign((await post<{ url: string }>("/api/admin/billing/portal")).url); } catch (e) { setMsg({ kind: "error", text: errorText(e) }); }
              }}>Manage payment methods</button></p>
            )}
          </div>
        </div>
      )}

      <h2>Invoices</h2>
      {o.invoices.length === 0 ? <p className="muted">No invoices yet.</p> : (
        <table>
          <thead><tr><th>Number</th><th>Period</th><th>Type</th><th>Total</th><th>Status</th><th>Due</th><th /></tr></thead>
          <tbody>{o.invoices.map((i) => (
            <tr key={i.id}>
              <td>{i.number}</td><td>{i.period_start.slice(0, 7)}</td><td>{i.kind}</td>
              <td>{fromMinor(i.total_minor, i.currency)}</td><td><StatusBadge status={i.status} /></td><td>{when(i.due_at)}</td>
              <td className="actions">
                <a href={`/api/admin/billing/invoices/${i.id}/html`} target="_blank" rel="noopener noreferrer">View</a>
                {i.hosted_url && i.status === "open" && <> · <a href={i.hosted_url} target="_blank" rel="noopener noreferrer">Pay</a></>}
              </td>
            </tr>))}</tbody>
        </table>
      )}
    </>
  );
}

function AccountForm({ account, onSave, busy }: {
  account: Overview["account"]; busy: boolean; onSave: (body: Record<string, unknown>) => Promise<void>;
}) {
  const [country, setCountry] = useState(account?.country ?? "");
  const [email, setEmail] = useState(account?.billing_email ?? "");
  const [company, setCompany] = useState(account?.company_name ?? "");
  const [tax, setTax] = useState(account?.tax_id ?? "");
  return (
    <form className="card form" onSubmit={(e) => {
      e.preventDefault();
      void onSave({ country, billing_email: email || null, company_name: company || null, tax_id: tax || null });
    }}>
      <h3>Billing details</h3>
      <label>Country
        <select required value={country} disabled={account?.currency_locked} onChange={(e) => setCountry(e.target.value)}>
          <option value="">Select…</option>
          {COUNTRIES.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
        </select>
      </label>
      {country && <p className="small muted">Billed in {currencyFor(country)}{account?.currency_locked ? " (fixed after your first invoice)" : ""}.</p>}
      <label>Billing email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
      <label>Company name<input value={company} maxLength={200} onChange={(e) => setCompany(e.target.value)} /></label>
      <label>Tax ID (GSTIN / VAT)<input value={tax} maxLength={40} onChange={(e) => setTax(e.target.value)} /></label>
      <button disabled={busy}>Save billing details</button>
    </form>
  );
}

function SpendCap({ currency, cap, busy, onSave }: {
  currency: Currency | null; cap: number | null; busy: boolean; onSave: (v: number | null) => Promise<void>;
}) {
  const [text, setText] = useState(cap === null ? "" : String(cap / 1_000_000));
  return (
    <form className="inline" onSubmit={(e) => { e.preventDefault(); void onSave(text.trim() ? toMicros(text) : null); }}>
      <label className="inline">Monthly overage cap ({currency ?? "currency"})
        <input inputMode="decimal" placeholder="No cap" value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <button disabled={busy || !currency} className="secondary">Save cap</button>
    </form>
  );
}
