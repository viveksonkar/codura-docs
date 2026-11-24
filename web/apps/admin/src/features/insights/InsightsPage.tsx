/**
 * Basic Tenant insights (M2-11, FR-72): usage vs limits with a projection, conversations by
 * Channel and language, the unanswered-questions list (fallback + 👎) and feedback ratio.
 * Visitor emails (FR-54, ADR-0023) are a Tenant Admin-only section.
 */
import { useState } from "react";
import { LANGUAGES, del } from "../../api";
import { Link } from "../../router";
import { Alert, errorText, useData, when } from "../../ui";

interface Summary {
  days: number;
  usage: { plan: string; meters: { meter: string; used: number; limit: number | null; projected: number }[] };
  conversations_by_channel: { channel: string; count: number }[];
  questions_by_language: { language: string; count: number }[];
  conversations_per_day: { day: string; count: number }[];
  answers: { answered: number; fallback: number; thumbs_up: number; thumbs_down: number; feedback_ratio: number | null; avg_latency_ms: number | null };
}
interface Unanswered {
  question: string;
  language: string | null;
  reason: "fallback" | "thumbs_down";
  feedback_note: string | null;
  created_at: string;
  conversation_id: string;
  channel: string;
  assistant_name: string;
}

const METER_LABELS: Record<string, string> = { answer: "Answers", page_ingested: "Pages ingested", page_ocr: "OCR pages" };

function Bars({ rows, label }: { rows: { key: string; count: number }[]; label: (k: string) => string }) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  if (!rows.length) return <p className="muted">No data yet.</p>;
  return (
    <table className="bars">
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}>
            <th scope="row">{label(r.key)}</th>
            <td>
              <span className="bar" style={{ width: `${Math.round((r.count / max) * 100)}%` }} />
            </td>
            <td className="num">{r.count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function InsightsPage({ canEdit }: { canEdit: boolean }) {
  const [days, setDays] = useState(30);
  const summary = useData<Summary>(`/api/admin/insights/summary?days=${days}`, [days]);
  const unanswered = useData<{ items: Unanswered[] }>(`/api/admin/insights/unanswered?days=${days}`, [days]);
  const s = summary.data;
  return (
    <>
      <div className="spread">
        <h1>Insights</h1>
        <select aria-label="Period" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </select>
      </div>
      <Alert>{summary.error}</Alert>
      {s && (
        <>
          <div className="cards">
            {s.usage.meters.map((m) => (
              <div className="card stat" key={m.meter}>
                <div className="muted">{METER_LABELS[m.meter] ?? m.meter} this month</div>
                <div className="big">
                  {m.used}
                  {m.limit ? <span className="muted"> / {m.limit}</span> : null}
                </div>
                {m.limit ? <progress max={m.limit} value={Math.min(m.used, m.limit)} /> : null}
                <div className={`muted small${m.limit && m.projected > m.limit ? " danger" : ""}`}>Projected by month end: {m.projected}</div>
              </div>
            ))}
            <div className="card stat">
              <div className="muted">Answer feedback ({s.days} days)</div>
              <div className="big">{s.answers.feedback_ratio == null ? "—" : `${Math.round(s.answers.feedback_ratio * 100)}% 👍`}</div>
              <div className="muted small">
                👍 {s.answers.thumbs_up} · 👎 {s.answers.thumbs_down} · unanswered {s.answers.fallback} of {s.answers.answered + s.answers.fallback}
              </div>
              {s.answers.avg_latency_ms != null && <div className="muted small">Average answer time {(s.answers.avg_latency_ms / 1000).toFixed(1)} s</div>}
            </div>
          </div>
          <div className="split">
            <div>
              <h2>Conversations by channel</h2>
              <Bars rows={s.conversations_by_channel.map((r) => ({ key: r.channel, count: r.count }))} label={(k) => k} />
            </div>
            <div>
              <h2>Questions by language</h2>
              <Bars rows={s.questions_by_language.map((r) => ({ key: r.language, count: r.count }))} label={(k) => LANGUAGES[k] ?? k} />
            </div>
          </div>
        </>
      )}
      <h2>Unanswered questions</h2>
      <p className="muted">Questions your documents couldn't answer, or answers Visitors rated 👎. Add or fix documents to close these gaps.</p>
      {unanswered.data?.items.length === 0 && <p className="muted">Nothing here. 🎉</p>}
      <table>
        <tbody>
          {unanswered.data?.items.map((u, i) => (
            <tr key={i}>
              <td dir="auto">
                <Link to={`/conversations/${u.conversation_id}`}>{u.question}</Link>
                {u.feedback_note && (
                  <div className="muted small" dir="auto">
                    “{u.feedback_note}”
                  </div>
                )}
              </td>
              <td>{u.reason === "fallback" ? "No answer" : "👎"}</td>
              <td>{u.language ?? "—"}</td>
              <td>{u.assistant_name}</td>
              <td>{when(u.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {canEdit && <VisitorEmails />}
    </>
  );
}

function VisitorEmails() {
  const list = useData<{ items: { conversation_id: string; email: string; consent_at: string; channel: string; assistant_name: string; started_at: string }[] }>(
    "/api/admin/visitor-emails",
  );
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  return (
    <>
      <h2>Visitor emails</h2>
      <p className="muted">
        Emails Visitors chose to leave at the start of a chat. They are contact data only, never used to identify a Visitor. Only
        Tenant Admins can see them.
      </p>
      <div className="inline">
        <a className="button secondary" href="/api/admin/visitor-emails/export">
          Export CSV
        </a>
        <input type="email" placeholder="email@example.com" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="Email to delete" />
        <button
          type="button"
          className="danger"
          disabled={!email.includes("@")}
          onClick={async () => {
            if (!confirm(`Delete ${email} from every conversation?`)) return;
            try {
              await del(`/api/admin/visitor-emails?email=${encodeURIComponent(email)}`);
              setMsg({ kind: "ok", text: `Deleted ${email}.` });
              setEmail("");
              void list.reload();
            } catch (e) {
              setMsg({ kind: "error", text: errorText(e) });
            }
          }}
        >
          Delete on request
        </button>
      </div>
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
      <table>
        <thead>
          <tr>
            <th>Email</th>
            <th>Consent</th>
            <th>Assistant</th>
            <th>Channel</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {list.data?.items.map((r) => (
            <tr key={r.conversation_id}>
              <td>{r.email}</td>
              <td>{when(r.consent_at)}</td>
              <td>{r.assistant_name}</td>
              <td>{r.channel}</td>
              <td>
                <Link to={`/conversations/${r.conversation_id}`}>Conversation</Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
