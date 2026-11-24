/**
 * Conversation log viewer (M2-08, FR-35): filter by Assistant, Channel, language, feedback,
 * outcome and date; detail with Citations and the retrieval debug. Current Tenant only.
 * Visitor and Document text is untrusted: rendered as text, or as sanitised Markdown (SEC-07).
 */
import { useState } from "react";
import { LANGUAGES, get } from "../../api";
import { Link } from "../../router";
import { Alert, SafeMarkdown, errorText, useData, when } from "../../ui";

interface ConvRow {
  id: string;
  assistant_id: string;
  assistant_name: string;
  channel: string;
  language: string | null;
  started_at: string;
  last_message_at: string;
  message_count: number;
  has_visitor_email: boolean;
  first_question: string | null;
  thumbs_up: number;
  thumbs_down: number;
  fallbacks: number;
}
interface Msg {
  id: string;
  role: "user" | "assistant";
  content: string;
  language: string | null;
  standalone_query: string | null;
  citations: { n: number; title: string; page: number | null; heading_path: string[]; snippet: string | null }[];
  retrieval: { chunk_id?: string; title?: string; score?: number; rank?: number; [k: string]: unknown }[] | null;
  outcome: string | null;
  latency_ms: number | null;
  first_token_ms: number | null;
  finish_reason: string | null;
  feedback: 1 | -1 | null;
  feedback_note: string | null;
  created_at: string;
}

const CHANNEL_LABELS: Record<string, string> = { hosted: "Hosted page", widget: "Widget", wordpress: "WordPress", android: "Android", api: "API", playground: "Playground" };

export function buildQuery(f: Record<string, string>, cursor?: string | null): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v) q.set(k, v);
  if (cursor) q.set("cursor", cursor);
  q.set("limit", "25");
  return `/api/admin/conversations?${q.toString()}`;
}

export function ConversationsPage() {
  const assistants = useData<{ items: { id: string; name: string }[] }>("/api/admin/assistants");
  const [f, setF] = useState({ assistant_id: "", channel: "", language: "", feedback: "", outcome: "", from: "", to: "" });
  const [extra, setExtra] = useState<ConvRow[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const page = useData<{ items: ConvRow[]; next_cursor: string | null }>(buildQuery(f), [JSON.stringify(f)]);
  const rows = [...(page.data?.items ?? []), ...extra];
  const next = extra.length ? cursor : page.data?.next_cursor ?? null;
  const set = (k: keyof typeof f, v: string) => {
    setExtra([]);
    setCursor(null);
    setF({ ...f, [k]: v });
  };
  return (
    <>
      <h1>Conversations</h1>
      <div className="filters inline">
        <select aria-label="Assistant" value={f.assistant_id} onChange={(e) => set("assistant_id", e.target.value)}>
          <option value="">All assistants</option>
          {assistants.data?.items.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        <select aria-label="Channel" value={f.channel} onChange={(e) => set("channel", e.target.value)}>
          <option value="">All channels</option>
          {Object.entries(CHANNEL_LABELS).map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
        <select aria-label="Language" value={f.language} onChange={(e) => set("language", e.target.value)}>
          <option value="">All languages</option>
          {Object.entries(LANGUAGES).map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
        <select aria-label="Feedback" value={f.feedback} onChange={(e) => set("feedback", e.target.value)}>
          <option value="">Any feedback</option>
          <option value="down">👎</option>
          <option value="up">👍</option>
          <option value="any">Rated</option>
          <option value="none">Not rated</option>
        </select>
        <select aria-label="Outcome" value={f.outcome} onChange={(e) => set("outcome", e.target.value)}>
          <option value="">Any outcome</option>
          <option value="fallback">Unanswered (fallback)</option>
          <option value="answered">Answered</option>
          <option value="limit">Limit reached</option>
          <option value="error">Error</option>
        </select>
        <label>
          From <input type="date" value={f.from} onChange={(e) => set("from", e.target.value)} />
        </label>
        <label>
          To <input type="date" value={f.to} onChange={(e) => set("to", e.target.value)} />
        </label>
      </div>
      <Alert>{page.error || err}</Alert>
      {rows.length === 0 && !page.loading && <p className="muted">No conversations match.</p>}
      <table>
        <thead>
          <tr>
            <th>Last message</th>
            <th>First question</th>
            <th>Assistant</th>
            <th>Channel</th>
            <th>Lang</th>
            <th>Msgs</th>
            <th>Feedback</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id}>
              <td>{when(c.last_message_at)}</td>
              <td dir="auto">
                <Link to={`/conversations/${c.id}`}>{(c.first_question ?? "(empty)").slice(0, 90)}</Link>
                {c.fallbacks > 0 && <span className="badge badge-failed"> unanswered</span>}
                {c.has_visitor_email && <span className="badge"> email</span>}
              </td>
              <td>{c.assistant_name}</td>
              <td>{CHANNEL_LABELS[c.channel] ?? c.channel}</td>
              <td>{c.language ?? "—"}</td>
              <td>{c.message_count}</td>
              <td>
                {c.thumbs_up > 0 && `👍${c.thumbs_up} `}
                {c.thumbs_down > 0 && `👎${c.thumbs_down}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {next && (
        <button
          type="button"
          className="secondary"
          onClick={async () => {
            try {
              const more = await get<{ items: ConvRow[]; next_cursor: string | null }>(buildQuery(f, next));
              setExtra([...extra, ...more.items]);
              setCursor(more.next_cursor);
            } catch (e) {
              setErr(errorText(e));
            }
          }}
        >
          Load more
        </button>
      )}
    </>
  );
}

export function ConversationDetail({ id }: { id: string }) {
  const { data, error } = useData<ConvRow & { messages: Msg[] }>(`/api/admin/conversations/${encodeURIComponent(id)}`);
  const [debug, setDebug] = useState<string | null>(null);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return <div className="loading">Loading…</div>;
  return (
    <>
      <p>
        <Link to="/conversations">← Conversations</Link>
      </p>
      <h1>Conversation</h1>
      <p className="muted">
        {data.assistant_name} · {CHANNEL_LABELS[data.channel] ?? data.channel} · started {when(data.started_at)} · {data.message_count} messages
        {data.has_visitor_email && " · Visitor left an email (see Insights → Visitor emails)"}
      </p>
      <div className="chat transcript">
        {data.messages.map((m) => (
          <div key={m.id} className={`msg msg-${m.role}`}>
            {m.role === "user" ? (
              <div dir="auto" className="plain">
                {m.content}
              </div>
            ) : (
              <>
                <SafeMarkdown text={m.content} />
                {m.citations.length > 0 && (
                  <ol className="cites">
                    {m.citations.map((c) => (
                      <li key={c.n} dir="auto">
                        <strong>{c.title}</strong>
                        {c.page != null && ` · p. ${c.page}`}
                        {c.snippet && <div className="muted">{c.snippet}</div>}
                      </li>
                    ))}
                  </ol>
                )}
                <div className="muted small">
                  {m.outcome ?? "—"} · {m.finish_reason ?? ""} · {m.language ?? ""}
                  {m.latency_ms != null && ` · ${m.latency_ms} ms`}
                  {m.feedback === 1 && " · 👍"}
                  {m.feedback === -1 && " · 👎"}
                  {m.feedback_note && <span dir="auto"> “{m.feedback_note}”</span>}{" "}
                  <button type="button" className="link" onClick={() => setDebug(debug === m.id ? null : m.id)}>
                    {debug === m.id ? "Hide retrieval" : "Retrieval debug"}
                  </button>
                </div>
                {debug === m.id && (
                  <div className="card small">
                    {m.standalone_query && (
                      <p dir="auto">
                        <strong>Search query:</strong> {m.standalone_query}
                      </p>
                    )}
                    {(m.retrieval ?? []).length === 0 ? (
                      <p className="muted">No chunks retrieved.</p>
                    ) : (
                      <table>
                        <thead>
                          <tr>
                            {Object.keys(m.retrieval![0]!).map((k) => (
                              <th key={k}>{k}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {m.retrieval!.map((r, i) => (
                            <tr key={i}>
                              {Object.values(r).map((v, j) => (
                                <td key={j} dir="auto">
                                  {typeof v === "number" ? v.toFixed(4).replace(/\.?0+$/, "") : String(v ?? "")}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
