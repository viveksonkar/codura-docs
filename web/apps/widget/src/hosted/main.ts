/**
 * Hosted Chat Page (FR-46, channels.md §2): /c/{tenant-slug}/{assistant-slug}[?lang=hi][&embed=1].
 * Fetches the Assistant's hosted Channel Key and renders the Widget inline at full viewport.
 */
export interface HostedTarget {
  tenant: string;
  assistant: string;
  lang: string | null;
  embed: boolean;
}

const SLUG = /^[A-Za-z0-9-]{1,63}$/;

export function parseHostedUrl(pathname: string, search: string): HostedTarget | null {
  const parts = pathname.split("/").filter(Boolean);
  if (parts.length !== 3 || parts[0] !== "c") return null;
  const [, tenant, assistant] = parts as [string, string, string];
  if (!SLUG.test(tenant) || !SLUG.test(assistant)) return null;
  const q = new URLSearchParams(search);
  const lang = q.get("lang");
  return { tenant, assistant, lang: lang && /^[a-z]{2}(-[A-Za-z]{2})?$/.test(lang) ? lang : null, embed: q.get("embed") === "1" };
}

export async function mountHosted(doc: Document = document, loc: Location = window.location): Promise<void> {
  const target = parseHostedUrl(loc.pathname, loc.search);
  const host = doc.getElementById("codura-hosted")!;
  const fail = () => {
    host.innerHTML = "";
    const p = doc.createElement("p");
    p.className = "missing";
    p.textContent = "This chat page does not exist.";
    host.appendChild(p);
  };
  if (!target) return fail();
  const res = await fetch(`/api/v1/hosted/${encodeURIComponent(target.tenant)}/${encodeURIComponent(target.assistant)}`, {
    credentials: "omit",
  });
  if (!res.ok) return fail();
  const { channel_key, api } = (await res.json()) as { channel_key: string; api: string };
  const el = doc.createElement("codura-chat");
  el.setAttribute("key", channel_key);
  el.setAttribute("api", loc.origin || api);
  el.setAttribute("mode", "inline");
  el.setAttribute("fullpage", "");
  el.setAttribute("lang", target.lang ?? "auto");
  if (target.embed) el.setAttribute("hide-header", "");
  el.addEventListener("codura:ready", (e) => {
    const name = (e as CustomEvent<{ assistant?: string }>).detail?.assistant;
    if (name) doc.title = name;
  });
  host.innerHTML = "";
  host.appendChild(el);
}

if (typeof window !== "undefined" && document.getElementById("codura-hosted")) {
  void mountHosted();
}
