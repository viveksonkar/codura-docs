/**
 * Channels (M2-03, FR-34): per Assistant, the Hosted Chat Page toggle (link + QR), Channel Keys with
 * Allowed Origins, a ready-to-paste Widget snippet and the local demo page. Secret Keys below.
 */
import qrcode from "qrcode-generator";
import { useEffect, useMemo, useState } from "react";
import { patch, post } from "../../api";
import { Alert, errorText, useData, when } from "../../ui";

interface Assistant {
  id: string;
  name: string;
  slug: string;
  hosted_enabled: boolean;
}
interface Key {
  id: string;
  kind: "channel" | "secret";
  prefix: string;
  name: string;
  channel: string | null;
  assistant_id: string | null;
  allowed_origins: string[];
  active: boolean;
  last_used_at: string | null;
  created_at: string;
}

const ORIGIN = /^https?:\/\/[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:\d{1,5})?$/;
const CHANNELS: Record<string, string> = {
  widget: "Website widget",
  wordpress: "WordPress plugin",
  android: "Android app",
  api: "Server / custom API",
};

export function parseOrigins(text: string): { origins: string[]; invalid: string[] } {
  const origins: string[] = [];
  const invalid: string[] = [];
  for (const raw of text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean)) {
    const o = raw.replace(/\/+$/, "").toLowerCase();
    if (ORIGIN.test(o)) {
      if (!origins.includes(o)) origins.push(o);
    } else invalid.push(raw);
  }
  return { origins, invalid };
}

export function widgetSnippet(key: string, api: string, mode: "launcher" | "inline" = "launcher"): string {
  return mode === "launcher"
    ? `<script src="${api}/widget/v1/widget.js" defer\n        data-key="${key}" data-api="${api}"\n        data-mode="launcher" data-position="right" data-lang="auto"></script>`
    : `<script src="${api}/widget/v1/widget.js" defer></script>\n<codura-chat key="${key}" api="${api}" mode="inline" style="display:block;height:600px"></codura-chat>`;
}

function Copy({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="secondary"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          /* clipboard blocked: the text is selectable */
        }
      }}
    >
      {done ? "Copied" : label}
    </button>
  );
}

function Qr({ text }: { text: string }) {
  const svg = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(text);
    qr.make();
    return qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
  }, [text]);
  // Generated locally from our own URL, not from user or model content.
  return <div className="qr" aria-label="QR code for the chat page" role="img" dangerouslySetInnerHTML={{ __html: svg }} />;
}

function HostedCard({ assistant, tenantSlug, onChange }: { assistant: Assistant; tenantSlug: string; onChange: () => void }) {
  const [err, setErr] = useState("");
  const url = `${window.location.origin}/c/${tenantSlug}/${assistant.slug}`;
  return (
    <div className="card">
      <div className="spread">
        <h3>Hosted Chat Page</h3>
        <label className="toggle">
          <input
            type="checkbox"
            checked={assistant.hosted_enabled}
            onChange={async (e) => {
              try {
                await patch(`/api/admin/assistants/${assistant.id}`, { hosted_enabled: e.target.checked });
                setErr("");
                onChange();
              } catch (x) {
                setErr(errorText(x));
              }
            }}
          />{" "}
          {assistant.hosted_enabled ? "On" : "Off"}
        </label>
      </div>
      <p className="muted">A ready-made chat page you can share as a link or QR code. No website needed.</p>
      <Alert>{err}</Alert>
      {assistant.hosted_enabled && (
        <div className="split">
          <div>
            <p>
              <a href={url} target="_blank" rel="noreferrer">
                {url}
              </a>
            </p>
            <div className="inline">
              <Copy text={url} label="Copy link" />
              <Copy text={`<iframe src="${url}?embed=1" style="width:100%;height:600px;border:0" title="${assistant.name}"></iframe>`} label="Copy iframe" />
            </div>
            <p className="muted">Add <code>?lang=hi</code> to force a language, <code>?embed=1</code> to hide the header.</p>
          </div>
          <Qr text={url} />
        </div>
      )}
    </div>
  );
}

function OriginsEditor({ k, onSaved }: { k: Key; onSaved: () => void }) {
  const [text, setText] = useState(k.allowed_origins.join("\n"));
  const [err, setErr] = useState("");
  const parsed = parseOrigins(text);
  return (
    <div className="form">
      <label>
        Allowed Origins (one per line)
        <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="https://www.example.com" />
      </label>
      {parsed.invalid.length > 0 && <Alert>Not an origin: {parsed.invalid.join(", ")} (use scheme://host[:port], no path)</Alert>}
      <Alert>{err}</Alert>
      <button
        type="button"
        disabled={parsed.invalid.length > 0}
        onClick={async () => {
          try {
            await patch(`/api/admin/channel-keys/${k.id}`, { allowed_origins: parsed.origins });
            setErr("");
            onSaved();
          } catch (e) {
            setErr(errorText(e));
          }
        }}
      >
        Save origins
      </button>
    </div>
  );
}

function KeyRow({ k, onChange }: { k: Key; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  const originBound = k.channel === "widget" || k.channel === "wordpress";
  return (
    <>
      <tr className={k.active ? "" : "revoked"}>
        <td>
          <code>{k.prefix}…</code>
        </td>
        <td>{k.channel === "hosted" ? "Hosted page (system)" : CHANNELS[k.channel ?? ""] ?? k.kind}</td>
        <td>{k.name || "—"}</td>
        <td>
          {originBound ? (k.allowed_origins.length ? k.allowed_origins.join(", ") : <span className="danger">none: browsers refused</span>) : "—"}
        </td>
        <td>{when(k.last_used_at)}</td>
        <td className="actions">
          {k.active && originBound && (
            <button type="button" className="link" onClick={() => setEditing(!editing)}>
              {editing ? "Close" : "Origins"}
            </button>
          )}
          {k.active && k.channel !== "hosted" && (
            <button
              type="button"
              className="link danger"
              onClick={async () => {
                if (!confirm("Revoke this key? Sites and apps using it stop working immediately.")) return;
                await post(`/api/admin/channel-keys/${k.id}/revoke`);
                onChange();
              }}
            >
              Revoke
            </button>
          )}
          {!k.active && "revoked"}
        </td>
      </tr>
      {editing && (
        <tr>
          <td colSpan={6}>
            <OriginsEditor
              k={k}
              onSaved={() => {
                setEditing(false);
                onChange();
              }}
            />
          </td>
        </tr>
      )}
    </>
  );
}

function NewKey({ assistant, onCreated }: { assistant: Assistant; onCreated: (key: string, channel: string) => void }) {
  const [channel, setChannel] = useState("widget");
  const [name, setName] = useState("");
  const [origins, setOrigins] = useState("");
  const [err, setErr] = useState("");
  const parsed = parseOrigins(origins);
  const originBound = channel === "widget" || channel === "wordpress";
  return (
    <div className="card form">
      <h3>New Channel Key</h3>
      <div className="inline">
        <select value={channel} onChange={(e) => setChannel(e.target.value)} aria-label="Channel">
          {Object.entries(CHANNELS).map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
        <input placeholder="Label (e.g. Main website)" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      {originBound && (
        <label>
          Allowed Origins: the websites that may use this key (one per line)
          <textarea rows={2} value={origins} onChange={(e) => setOrigins(e.target.value)} placeholder="https://www.example.com" />
        </label>
      )}
      {originBound && parsed.invalid.length > 0 && <Alert>Not an origin: {parsed.invalid.join(", ")}</Alert>}
      {originBound && parsed.origins.length === 0 && <p className="muted">Without an origin, browsers can't use this key until you add one.</p>}
      <Alert>{err}</Alert>
      <button
        type="button"
        disabled={originBound && parsed.invalid.length > 0}
        onClick={async () => {
          try {
            const r = await post<{ key: string }>("/api/admin/channel-keys", {
              kind: "channel",
              channel,
              assistant_id: assistant.id,
              name,
              allowed_origins: originBound ? parsed.origins : [],
            });
            setErr("");
            setName("");
            setOrigins("");
            onCreated(r.key, channel);
          } catch (e) {
            setErr(errorText(e));
          }
        }}
      >
        Create key
      </button>
    </div>
  );
}

export function ChannelsPage({ tenantSlug }: { tenantSlug: string }) {
  const assistants = useData<{ items: Assistant[] }>("/api/admin/assistants");
  const [selected, setSelected] = useState("");
  const assistant = assistants.data?.items.find((a) => a.id === selected) ?? assistants.data?.items[0];
  const keys = useData<{ items: Key[] }>(assistant ? `/api/admin/channel-keys?assistant_id=${assistant.id}` : null, [assistant?.id]);
  const [created, setCreated] = useState<{ key: string; channel: string } | null>(null);
  const api = window.location.origin;
  useEffect(() => setCreated(null), [assistant?.id]);

  if (assistants.data && assistants.data.items.length === 0) {
    return (
      <>
        <h1>Channels</h1>
        <p>Create an Assistant first; each Channel connects to one Assistant.</p>
      </>
    );
  }
  return (
    <>
      <h1>Channels</h1>
      <p className="muted">
        Channel Keys (pk_…) are publishable: they can only chat with one Assistant, from the places you allow. Revoke a key to cut a
        site or app off instantly.
      </p>
      <label className="inline">
        Assistant{" "}
        <select value={assistant?.id ?? ""} onChange={(e) => setSelected(e.target.value)}>
          {assistants.data?.items.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </label>
      {assistant && (
        <>
          <HostedCard assistant={assistant} tenantSlug={tenantSlug} onChange={() => { void assistants.reload(); void keys.reload(); }} />
          {created && (
            <div className="card">
              <Alert kind="ok">
                Copy this key now: it is shown only once. <code className="secret">{created.key}</code> <Copy text={created.key} />
              </Alert>
              {created.channel === "widget" && (
                <>
                  <h3>Add the widget to your website</h3>
                  <p className="muted">Paste before &lt;/body&gt;. For an inline chat, use the second snippet where the chat should appear.</p>
                  <pre className="snippet">{widgetSnippet(created.key, api)}</pre>
                  <Copy text={widgetSnippet(created.key, api)} label="Copy launcher snippet" />
                  <pre className="snippet">{widgetSnippet(created.key, api, "inline")}</pre>
                  <Copy text={widgetSnippet(created.key, api, "inline")} label="Copy inline snippet" />
                  <p>
                    <a href={`/demo/?key=${encodeURIComponent(created.key)}`} target="_blank" rel="noreferrer">
                      Open the demo page
                    </a>{" "}
                    <span className="muted">(local testing only)</span>
                  </p>
                </>
              )}
            </div>
          )}
          <NewKey assistant={assistant} onCreated={(key, channel) => { setCreated({ key, channel }); void keys.reload(); }} />
          <table>
            <thead>
              <tr>
                <th>Key</th>
                <th>Channel</th>
                <th>Label</th>
                <th>Allowed Origins</th>
                <th>Last used</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {keys.data?.items.map((k) => (
                <KeyRow key={k.id} k={k} onChange={() => void keys.reload()} />
              ))}
            </tbody>
          </table>
        </>
      )}
      <SecretKeys />
    </>
  );
}

function SecretKeys() {
  const all = useData<{ items: Key[] }>("/api/admin/channel-keys");
  const secrets = all.data?.items.filter((k) => k.kind === "secret") ?? [];
  const [created, setCreated] = useState("");
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  return (
    <>
      <h2>Secret Keys</h2>
      <p className="muted">Secret Keys (sk_…) use the admin API from your servers. Never put one in a web page or app.</p>
      <div className="inline">
        <input placeholder="Label" value={name} onChange={(e) => setName(e.target.value)} />
        <button
          type="button"
          onClick={async () => {
            try {
              const r = await post<{ key: string }>("/api/admin/channel-keys", { kind: "secret", name });
              setCreated(r.key);
              setErr("");
              setName("");
              void all.reload();
            } catch (e) {
              setErr(errorText(e));
            }
          }}
        >
          Create Secret Key
        </button>
      </div>
      <Alert>{err}</Alert>
      {created && (
        <Alert kind="ok">
          Copy this key now: it is shown only once. <code className="secret">{created}</code> <Copy text={created} />
        </Alert>
      )}
      {secrets.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Key</th>
              <th>Label</th>
              <th>Created</th>
              <th>Last used</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {secrets.map((k) => (
              <tr key={k.id} className={k.active ? "" : "revoked"}>
                <td>
                  <code>{k.prefix}…</code>
                </td>
                <td>{k.name || "—"}</td>
                <td>{when(k.created_at)}</td>
                <td>{when(k.last_used_at)}</td>
                <td>
                  {k.active ? (
                    <button type="button" className="link danger" onClick={async () => { await post(`/api/admin/channel-keys/${k.id}/revoke`); void all.reload(); }}>
                      Revoke
                    </button>
                  ) : (
                    "revoked"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
