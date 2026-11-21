import { useEffect, useRef, useState } from "react";
import { LANGUAGES, del, patch, post } from "../api";
import { Link, navigate } from "../router";
import { Alert, ReadOnlyNote, StatusBadge, bytes, errorText, useData, when } from "../ui";

interface Kb { id: string; name: string; description: string; document_count: number; ready_count: number }
interface Doc {
  id: string; title: string; filename: string; mime_type: string; size_bytes: number; status: string;
  error: string | null; warnings: string[]; language: string | null; page_count: number | null;
  pages_done: number; ocr_pages: number; chunk_count: number; created_at: string;
}

export function Dashboard({ canEdit }: { canEdit: boolean }) {
  const usage = useData<{ plan: string; meters: { meter: string; used: number; limit: number | null }[] }>("/api/admin/usage");
  const kbs = useData<{ items: Kb[] }>("/api/admin/knowledge-bases");
  const labels: Record<string, string> = { answer: "Answers", page_ingested: "Pages ingested", page_ocr: "OCR pages" };
  return (
    <>
      <h1>Overview</h1>
      <ReadOnlyNote canEdit={canEdit} />
      <div className="cards">
        {usage.data?.meters.map((m) => (
          <div className="card stat" key={m.meter}>
            <div className="muted">{labels[m.meter] ?? m.meter} this month</div>
            <div className="big">{m.used}{m.limit ? <span className="muted"> / {m.limit}</span> : null}</div>
            {m.limit ? <progress max={m.limit} value={m.used} /> : null}
          </div>
        ))}
      </div>
      <p className="muted">Plan: {usage.data?.plan ?? "…"} (trial limits until plans launch)</p>
      <h2>Knowledge Bases</h2>
      {kbs.data?.items.length === 0 && <p>No Knowledge Bases yet. <Link to="/knowledge-bases">Create one</Link> and upload documents.</p>}
      <ul>{kbs.data?.items.map((k) => <li key={k.id}><Link to={`/knowledge-bases/${k.id}`}>{k.name}</Link> — {k.ready_count}/{k.document_count} ready</li>)}</ul>
    </>
  );
}

export function KnowledgeBases({ canEdit }: { canEdit: boolean }) {
  const { data, error, reload } = useData<{ items: Kb[] }>("/api/admin/knowledge-bases");
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  return (
    <>
      <h1>Knowledge Bases</h1>
      <ReadOnlyNote canEdit={canEdit} />
      <Alert>{error || err}</Alert>
      {canEdit && (
        <form className="inline" onSubmit={async (e) => {
          e.preventDefault();
          try { await post("/api/admin/knowledge-bases", { name }); setName(""); void reload(); } catch (x) { setErr(errorText(x)); }
        }}>
          <input placeholder="New Knowledge Base name" required value={name} onChange={(e) => setName(e.target.value)} />
          <button>Create</button>
        </form>
      )}
      <table>
        <thead><tr><th>Name</th><th>Documents</th><th>Ready</th></tr></thead>
        <tbody>
          {data?.items.map((k) => (
            <tr key={k.id}><td><Link to={`/knowledge-bases/${k.id}`}>{k.name}</Link></td><td>{k.document_count}</td><td>{k.ready_count}</td></tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function KnowledgeBaseDetail({ id, canEdit }: { id: string; canEdit: boolean }) {
  const kb = useData<Kb>(`/api/admin/knowledge-bases/${id}`);
  const docs = useData<{ items: Doc[] }>(`/api/admin/knowledge-bases/${id}/documents`);
  const langs = useData<{ installed: string[] }>("/api/admin/ocr/languages");
  const [ocr, setOcr] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [chunks, setChunks] = useState<{ doc: string; items: { ordinal: number; text: string; page_start: number | null; ocr_confidence: number | null }[] } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Live status while anything is still processing (FR-04).
  const pending = docs.data?.items.some((d) => d.status === "pending" || d.status === "processing");
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(() => void docs.reload(), 2000);
    return () => clearInterval(t);
  }, [pending, docs.reload]);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("file", f));
    if (ocr) fd.append("ocr_languages", ocr);
    setBusy(true);
    try {
      const r = await post<{ items: { filename: string; status: string; error?: { message: string } }[] }>(
        `/api/admin/knowledge-bases/${id}/documents`, fd);
      const rejected = r.items.filter((i) => i.status === "rejected" || i.status === "duplicate");
      setMsg(rejected.length
        ? { kind: "error", text: rejected.map((i) => `${i.filename}: ${i.error?.message ?? i.status}`).join("; ") }
        : { kind: "ok", text: `Uploaded ${r.items.length} file(s). Processing…` });
      void docs.reload();
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <>
      <p><Link to="/knowledge-bases">← Knowledge Bases</Link></p>
      <h1>{kb.data?.name ?? "…"}</h1>
      <ReadOnlyNote canEdit={canEdit} />
      {canEdit && (
        <div className="card dropzone"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); void upload(e.dataTransfer.files); }}>
          <p><strong>Upload documents</strong> — PDF (incl. scanned), DOCX, TXT, Markdown, HTML, PNG/JPEG/TIFF/WebP. Drag & drop or choose files.</p>
          <div className="inline">
            <input ref={fileRef} type="file" multiple disabled={busy} onChange={(e) => void upload(e.target.files)} />
            <label className="inline">OCR languages
              <select value={ocr} onChange={(e) => setOcr(e.target.value)}>
                <option value="">Organisation default</option>
                {langs.data?.installed.map((l) => <option key={l} value={l}>{l}</option>)}
                {langs.data?.installed.includes("hin") && <option value="eng+hin">eng+hin</option>}
              </select>
            </label>
          </div>
          {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
        </div>
      )}
      <table>
        <thead><tr><th>Document</th><th>Status</th><th>Language</th><th>Pages</th><th>OCR</th><th>Chunks</th><th>Size</th><th /></tr></thead>
        <tbody>
          {docs.data?.items.map((d) => (
            <tr key={d.id}>
              <td title={d.filename}>{d.title}<div className="muted small">{d.filename}</div>
                {d.warnings.length > 0 && <div className="warn small">⚠ {d.warnings.join(", ")}</div>}
                {d.error && <div className="error small">{d.error}</div>}</td>
              <td><StatusBadge status={d.status} />{d.status === "processing" && d.page_count ? <div className="small">{d.pages_done}/{d.page_count} pages</div> : null}</td>
              <td>{d.language ? LANGUAGES[d.language] ?? d.language : "—"}</td>
              <td>{d.page_count ?? "—"}</td>
              <td>{d.ocr_pages || "—"}</td>
              <td>{d.chunk_count}</td>
              <td>{bytes(d.size_bytes)}</td>
              <td className="actions">
                <button className="link" onClick={async () => {
                  const r = await fetch(`/api/admin/documents/${d.id}/chunks`).then((x) => x.json());
                  setChunks({ doc: d.title, items: r.items });
                }}>Chunks</button>
                {canEdit && <button className="link" onClick={async () => { await post(`/api/admin/documents/${d.id}/reingest`, {}); void docs.reload(); }}>Re-ingest</button>}
                {canEdit && <button className="link danger" onClick={async () => {
                  if (confirm(`Delete ${d.title}? It will never appear in answers again.`)) { await del(`/api/admin/documents/${d.id}`); void docs.reload(); }
                }}>Delete</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {chunks && (
        <div className="card">
          <h3>Chunks — {chunks.doc} <button className="link" onClick={() => setChunks(null)}>close</button></h3>
          {chunks.items.map((c) => (
            <div key={c.ordinal} className="chunk">
              <div className="muted small">#{c.ordinal} {c.page_start ? `p.${c.page_start}` : ""} {c.ocr_confidence ? `OCR ${c.ocr_confidence}%` : ""}</div>
              <pre dir="auto">{c.text}</pre>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

export function Members({ canEdit, myId }: { canEdit: boolean; myId: string }) {
  const { data, error, reload } = useData<{ items: { user_id?: string; invite_id?: string; email: string; name?: string; role: string; status: string }[] }>("/api/admin/members");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("user");
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); setMsg({ kind: "ok", text: ok }); void reload(); } catch (e) { setMsg({ kind: "error", text: errorText(e) }); }
  };
  return (
    <>
      <h1>Users</h1>
      <p className="muted">Tenant Admins manage documents, assistants, users and billing. Tenant Users have read-only access.</p>
      <ReadOnlyNote canEdit={canEdit} />
      <Alert>{error}</Alert>
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
      {canEdit && (
        <form className="inline" onSubmit={(e) => { e.preventDefault(); void act(() => post("/api/admin/invites", { email, role }), `Invitation sent to ${email}`); setEmail(""); }}>
          <input type="email" required placeholder="email@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
          <select value={role} onChange={(e) => setRole(e.target.value)}><option value="user">Tenant User (read-only)</option><option value="admin">Tenant Admin</option></select>
          <button>Invite</button>
        </form>
      )}
      <table>
        <thead><tr><th>Email</th><th>Name</th><th>Role</th><th>Status</th><th /></tr></thead>
        <tbody>
          {data?.items.map((m) => (
            <tr key={m.user_id ?? m.invite_id}>
              <td>{m.email}</td><td>{m.name ?? ""}</td>
              <td>{canEdit && m.user_id && m.user_id !== myId ? (
                <select value={m.role} onChange={(e) => void act(() => patch(`/api/admin/members/${m.user_id}`, { role: e.target.value }), "Role updated")}>
                  <option value="user">Tenant User</option><option value="admin">Tenant Admin</option></select>
              ) : m.role === "admin" ? "Tenant Admin" : "Tenant User"}</td>
              <td><StatusBadge status={m.status} /></td>
              <td>{canEdit && m.user_id && m.user_id !== myId && <button className="link danger" onClick={() => void act(() => del(`/api/admin/members/${m.user_id}`), "User removed")}>Remove</button>}
                {canEdit && m.invite_id && <button className="link danger" onClick={() => void act(() => del(`/api/admin/invites/${m.invite_id}`), "Invitation revoked")}>Revoke</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function Settings({ canEdit, onChange }: { canEdit: boolean; onChange: () => void }) {
  const t = useData<{ name: string; slug: string; ocr_languages: string; default_language: string; status: string }>("/api/admin/tenant");
  const langs = useData<{ installed: string[] }>("/api/admin/ocr/languages");
  const [form, setForm] = useState({ name: "", ocr_languages: "", default_language: "en" });
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  useEffect(() => { if (t.data) setForm({ name: t.data.name, ocr_languages: t.data.ocr_languages, default_language: t.data.default_language }); }, [t.data]);
  const selected = form.ocr_languages.split("+").filter(Boolean);
  return (
    <>
      <h1>Organisation settings</h1>
      <ReadOnlyNote canEdit={canEdit} />
      <div className="card form">
        <label>Name<input disabled={!canEdit} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label>Slug<input disabled value={t.data?.slug ?? ""} /></label>
        <fieldset disabled={!canEdit}>
          <legend>OCR languages for scanned documents (pick the languages your scans are written in)</legend>
          {langs.data?.installed.length === 0 && <p className="muted">OCR engine not available in this environment.</p>}
          {langs.data?.installed.map((l) => (
            <label key={l} className="check"><input type="checkbox" checked={selected.includes(l)} onChange={(e) => {
              const next = e.target.checked ? [...selected, l] : selected.filter((x) => x !== l);
              setForm({ ...form, ocr_languages: next.join("+") });
            }} />{l}</label>
          ))}
        </fieldset>
        <label>Default answer language<select disabled={!canEdit} value={form.default_language} onChange={(e) => setForm({ ...form, default_language: e.target.value })}>
          {Object.entries(LANGUAGES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
        {canEdit && <button onClick={async () => {
          try { await patch("/api/admin/tenant", form); setMsg({ kind: "ok", text: "Saved" }); onChange(); } catch (e) { setMsg({ kind: "error", text: errorText(e) }); }
        }}>Save</button>}
      </div>
    </>
  );
}

export function Keys() {
  const keys = useData<{ items: { id: string; kind: string; prefix: string; name: string; channel: string | null; assistant_id: string | null; revoked_at: string | null; created_at: string }[] }>("/api/admin/channel-keys");
  const assistants = useData<{ items: { id: string; name: string }[] }>("/api/admin/assistants");
  const [form, setForm] = useState({ kind: "channel", channel: "widget", assistant_id: "", name: "", allowed_origins: "" });
  const [created, setCreated] = useState("");
  const [err, setErr] = useState("");
  return (
    <>
      <h1>API & Channel keys</h1>
      <p className="muted">Channel Keys (pk_…) are publishable and only allow chatting with one Assistant. Secret Keys (sk_…) access the admin API — never put them in a web page or app.</p>
      <div className="card form">
        <div className="inline">
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}><option value="channel">Channel Key</option><option value="secret">Secret Key</option></select>
          {form.kind === "channel" && <>
            <select value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>
              {["widget", "hosted", "api"].map((c) => <option key={c}>{c}</option>)}</select>
            <select value={form.assistant_id} onChange={(e) => setForm({ ...form, assistant_id: e.target.value })}>
              <option value="">Choose assistant…</option>{assistants.data?.items.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
          </>}
          <input placeholder="Label" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </div>
        {form.kind === "channel" && form.channel === "widget" && <label>Allowed origins (comma-separated, e.g. https://www.example.com)<input value={form.allowed_origins} onChange={(e) => setForm({ ...form, allowed_origins: e.target.value })} /></label>}
        <Alert>{err}</Alert>
        {created && <Alert kind="ok">Copy this key now — it is shown only once: <code className="secret">{created}</code></Alert>}
        <button onClick={async () => {
          try {
            const r = await post<{ key: string }>("/api/admin/channel-keys", { ...form, allowed_origins: form.allowed_origins.split(",").map((s) => s.trim()).filter(Boolean) });
            setCreated(r.key); setErr(""); void keys.reload();
          } catch (e) { setErr(errorText(e)); }
        }}>Create key</button>
      </div>
      <table>
        <thead><tr><th>Key</th><th>Type</th><th>Channel</th><th>Label</th><th>Created</th><th /></tr></thead>
        <tbody>{keys.data?.items.map((k) => (
          <tr key={k.id} className={k.revoked_at ? "revoked" : ""}>
            <td><code>{k.prefix}…</code></td><td>{k.kind}</td><td>{k.channel ?? "—"}</td><td>{k.name}</td><td>{when(k.created_at)}</td>
            <td>{k.revoked_at ? "revoked" : <button className="link danger" onClick={async () => { await post(`/api/admin/channel-keys/${k.id}/revoke`); void keys.reload(); }}>Revoke</button>}</td>
          </tr>))}</tbody>
      </table>
    </>
  );
}

export function NotVerified({ email }: { email: string }) {
  const [sent, setSent] = useState(false);
  return (
    <Alert kind="info">
      Verify your email to unlock uploads and assistants. We sent a link to {email}.{" "}
      {sent ? "Sent again." : <button className="link" onClick={async () => { await post("/api/auth/resend-verification", { email }); setSent(true); }}>Resend</button>}
    </Alert>
  );
}

export { navigate };
