import { type ChatEvent, type Citation, streamAnswer } from "@codura/chat-client";
import { useEffect, useRef, useState } from "react";
import { LANGUAGES, getCsrf, patch, post } from "../api";
import { Link, navigate } from "../router";
import { Alert, ReadOnlyNote, SafeMarkdown, errorText, useData } from "../ui";

interface Assistant {
  id: string; name: string; slug: string; knowledge_base_ids: string[]; system_prompt: string;
  default_language: string; welcome_text: Record<string, string>; fallback_text: Record<string, string>;
  limit_reached_text: Record<string, string>; retrieval: Record<string, number>; hosted_enabled: boolean;
  visitor_email: string; visitor_email_consent: Record<string, string>; privacy_url: string | null;
  theme: { title?: string; accent?: string; position?: "left" | "right"; logo_url?: string | null };
}

export function Assistants({ canEdit }: { canEdit: boolean }) {
  const { data, reload } = useData<{ items: Assistant[] }>("/api/admin/assistants");
  const kbs = useData<{ items: { id: string; name: string }[] }>("/api/admin/knowledge-bases");
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  return (
    <>
      <h1>Assistants</h1>
      <p className="muted">An Assistant answers from the Knowledge Bases you choose. The answering model is managed by the platform.</p>
      <ReadOnlyNote canEdit={canEdit} />
      <Alert>{err}</Alert>
      {canEdit && (
        <form className="inline" onSubmit={async (e) => {
          e.preventDefault();
          try {
            const a = await post<Assistant>("/api/admin/assistants", { name, knowledge_base_ids: kbs.data?.items.map((k) => k.id) ?? [] });
            void reload();
            navigate(`/assistants/${a.id}`);
          } catch (x) { setErr(errorText(x)); }
        }}>
          <input required placeholder="New assistant name" value={name} onChange={(e) => setName(e.target.value)} />
          <button>Create</button>
        </form>
      )}
      <table>
        <thead><tr><th>Name</th><th>Knowledge Bases</th><th>Default language</th></tr></thead>
        <tbody>{data?.items.map((a) => (
          <tr key={a.id}><td><Link to={`/assistants/${a.id}`}>{a.name}</Link></td><td>{a.knowledge_base_ids.length}</td><td>{LANGUAGES[a.default_language]}</td></tr>
        ))}</tbody>
      </table>
    </>
  );
}

function PerLanguage({ label, value, onChange, disabled }: { label: string; value: Record<string, string>; onChange: (v: Record<string, string>) => void; disabled: boolean }) {
  const [lang, setLang] = useState("default");
  return (
    <div className="perlang">
      <div className="inline">
        <strong>{label}</strong>
        <select value={lang} onChange={(e) => setLang(e.target.value)}>
          <option value="default">Default (all languages)</option>
          {Object.entries(LANGUAGES).map(([k, v]) => <option key={k} value={k}>{v}{value[k] ? " ✓" : ""}</option>)}
        </select>
      </div>
      <textarea dir="auto" disabled={disabled} rows={2} value={value[lang] ?? ""} placeholder="Leave empty to use the built-in text"
        onChange={(e) => onChange({ ...value, [lang]: e.target.value })} />
    </div>
  );
}

export function AssistantDetail({ id, canEdit }: { id: string; canEdit: boolean }) {
  const a = useData<Assistant>(`/api/admin/assistants/${id}`);
  const kbs = useData<{ items: { id: string; name: string }[] }>("/api/admin/knowledge-bases");
  const [form, setForm] = useState<Assistant | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  useEffect(() => { if (a.data) setForm(a.data); }, [a.data]);
  if (!form) return <p>Loading…</p>;
  const set = <K extends keyof Assistant>(k: K, v: Assistant[K]) => setForm({ ...form, [k]: v });
  return (
    <>
      <p><Link to="/assistants">← Assistants</Link></p>
      <h1>{form.name}</h1>
      <ReadOnlyNote canEdit={canEdit} />
      <div className="split">
        <div className="card form">
          <fieldset disabled={!canEdit}>
            <label>Name<input value={form.name} onChange={(e) => set("name", e.target.value)} /></label>
            <label>Slug (used in the hosted chat link)<input value={form.slug} onChange={(e) => set("slug", e.target.value)} /></label>
            <div><strong>Knowledge Bases</strong>
              {kbs.data?.items.map((k) => (
                <label key={k.id} className="check"><input type="checkbox" checked={form.knowledge_base_ids.includes(k.id)}
                  onChange={(e) => set("knowledge_base_ids", e.target.checked ? [...form.knowledge_base_ids, k.id] : form.knowledge_base_ids.filter((x) => x !== k.id))} />{k.name}</label>
              ))}
            </div>
            <label>Instructions (system prompt)<textarea rows={4} value={form.system_prompt} placeholder="e.g. You are the support assistant for Acme. Be friendly and brief."
              onChange={(e) => set("system_prompt", e.target.value)} /></label>
            <label>Default language<select value={form.default_language} onChange={(e) => set("default_language", e.target.value)}>
              {Object.entries(LANGUAGES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
            <PerLanguage label="Welcome text" disabled={!canEdit} value={form.welcome_text} onChange={(v) => set("welcome_text", v)} />
            <PerLanguage label="Fallback (when the answer is not in your documents)" disabled={!canEdit} value={form.fallback_text} onChange={(v) => set("fallback_text", v)} />
            <PerLanguage label="Limit reached" disabled={!canEdit} value={form.limit_reached_text} onChange={(v) => set("limit_reached_text", v)} />
            <label>Visitor email<select value={form.visitor_email} onChange={(e) => set("visitor_email", e.target.value)}>
              <option value="off">Off</option><option value="optional">Optional</option><option value="required">Required</option></select></label>
            {form.visitor_email !== "off" && <>
              <PerLanguage label="Consent text shown next to the email field" disabled={!canEdit} value={form.visitor_email_consent ?? {}} onChange={(v) => set("visitor_email_consent", v)} />
              <label>Privacy policy URL<input type="url" placeholder="https://www.example.com/privacy" value={form.privacy_url ?? ""}
                onChange={(e) => set("privacy_url", e.target.value || null)} /></label>
            </>}
            <fieldset className="theme">
              <legend>Chat appearance (Widget and Hosted page)</legend>
              <label>Title<input value={form.theme?.title ?? ""} placeholder={form.name} onChange={(e) => set("theme", { ...form.theme, title: e.target.value })} /></label>
              <label>Accent colour<input type="color" value={form.theme?.accent ?? "#2563eb"} onChange={(e) => set("theme", { ...form.theme, accent: e.target.value })} /></label>
              <label>Launcher position<select value={form.theme?.position ?? "right"} onChange={(e) => set("theme", { ...form.theme, position: e.target.value as "left" | "right" })}>
                <option value="right">Right</option><option value="left">Left</option></select></label>
              <label>Logo URL (https)<input type="url" value={form.theme?.logo_url ?? ""} onChange={(e) => set("theme", { ...form.theme, logo_url: e.target.value || null })} /></label>
            </fieldset>
            <label>Relevance threshold (0–1, lower = more permissive)
              <input type="number" step="0.05" min="0" max="1" value={form.retrieval.min_score ?? 0.45}
                onChange={(e) => set("retrieval", { ...form.retrieval, min_score: Number(e.target.value) })} /></label>
            <label className="check"><input type="checkbox" checked={form.hosted_enabled} onChange={(e) => set("hosted_enabled", e.target.checked)} />Enable the Hosted Chat Page (link and QR code on the <Link to="/channels">Channels</Link> page)</label>
          </fieldset>
          {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
          {canEdit && <button onClick={async () => {
            try {
              const clean = (o: Record<string, string>) => Object.fromEntries(Object.entries(o).filter(([, v]) => v.trim()));
              setForm(await patch<Assistant>(`/api/admin/assistants/${id}`, {
                ...form, welcome_text: clean(form.welcome_text), fallback_text: clean(form.fallback_text), limit_reached_text: clean(form.limit_reached_text),
                visitor_email_consent: clean(form.visitor_email_consent ?? {}) }));
              setMsg({ kind: "ok", text: "Saved" });
            } catch (e) { setMsg({ kind: "error", text: errorText(e) }); }
          }}>Save</button>}
        </div>
        <Playground assistantId={id} />
      </div>
    </>
  );
}

interface Turn { role: "user" | "assistant"; text: string; citations?: Citation[]; language?: string; finish?: string; debug?: { standalone_query: string; retrieval: Record<string, unknown>[] } }

export function Playground({ assistantId }: { assistantId: string }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [conversation, setConversation] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [showDebug, setShowDebug] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [turns]);

  const ask = async () => {
    const q = question.trim();
    if (!q || busy) return;
    setQuestion(""); setErr(""); setBusy(true);
    setTurns((t) => [...t, { role: "user", text: q }, { role: "assistant", text: "" }]);
    const update = (fn: (t: Turn) => Turn) => setTurns((all) => [...all.slice(0, -1), fn(all[all.length - 1]!)]);
    abort.current = new AbortController();
    try {
      for await (const ev of streamAnswer(`/api/admin/assistants/${assistantId}/playground/stream`,
        { question: q, conversation_id: conversation }, { headers: { "X-CSRF-Token": getCsrf() }, signal: abort.current.signal })) {
        handle(ev, update);
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const handle = (ev: ChatEvent, update: (fn: (t: Turn) => Turn) => void) => {
    if (ev.type === "meta") { setConversation(ev.conversation_id); update((t) => ({ ...t, language: ev.language })); }
    else if (ev.type === "delta") update((t) => ({ ...t, text: t.text + ev.text }));
    else if (ev.type === "citations") update((t) => ({ ...t, citations: ev.items }));
    else if (ev.type === "done") update((t) => ({ ...t, finish: ev.finish_reason }));
    else if (ev.type === "debug") update((t) => ({ ...t, debug: ev }));
    else if (ev.type === "error") setErr(ev.message);
  };

  return (
    <div className="card playground">
      <div className="inline spread"><h3>Playground</h3>
        <span><label className="check"><input type="checkbox" checked={showDebug} onChange={(e) => setShowDebug(e.target.checked)} />Show retrieval details</label>
          <button className="link" onClick={() => { setTurns([]); setConversation(null); }}>New conversation</button></span></div>
      <p className="muted small">Ask in any language: answers come back in the question's language. Each answer counts toward usage.</p>
      <div className="chat">
        {turns.map((t, i) => (
          <div key={i} className={`msg msg-${t.role}`}>
            {t.role === "assistant" ? (t.text ? <SafeMarkdown text={t.text} /> : <span className="muted">…</span>) : <div dir="auto">{t.text}</div>}
            {t.citations && t.citations.length > 0 && (
              <div className="cites">{t.citations.map((c) => (
                <span key={c.n} className="cite" title={c.snippet ?? ""}>[{c.n}] {c.title}{c.page ? `, p.${c.page}` : ""}</span>))}</div>)}
            {t.role === "assistant" && t.finish && (
              <div className="muted small">{t.language ? LANGUAGES[t.language] : ""} · {t.finish === "fallback" ? "no relevant documents found (fallback text)" : t.finish === "limit" ? "usage limit reached" : t.finish}</div>)}
            {showDebug && t.debug && (
              <details open><summary className="small">Retrieval</summary>
                <div className="small">Standalone query: <em dir="auto">{t.debug.standalone_query}</em></div>
                <table className="small"><thead><tr><th>chunk</th><th>vector</th><th>fts</th><th>rrf</th><th>used</th></tr></thead>
                  <tbody>{t.debug.retrieval.map((r, j) => (
                    <tr key={j}><td>{String(r.chunk_id).slice(-8)}</td><td>{fmt(r.vector_score)}</td><td>{fmt(r.fts_rank)}</td><td>{fmt(r.rrf)}</td><td>{r.used ? "✓" : ""}</td></tr>))}</tbody></table>
              </details>)}
          </div>
        ))}
        <div ref={end} />
      </div>
      <Alert>{err}</Alert>
      <form className="inline" onSubmit={(e) => { e.preventDefault(); void ask(); }}>
        <input dir="auto" placeholder="Ask a question…" value={question} maxLength={2000} onChange={(e) => setQuestion(e.target.value)} />
        {busy ? <button type="button" onClick={() => abort.current?.abort()}>Stop</button> : <button>Send</button>}
      </form>
    </div>
  );
}

function fmt(v: unknown): string {
  return typeof v === "number" ? v.toFixed(3) : "—";
}
