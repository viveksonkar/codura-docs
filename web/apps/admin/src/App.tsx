import { useCallback, useEffect, useState } from "react";
import { type Me, ApiError, get, post, setCsrf } from "./api";
import { AssistantDetail, Assistants } from "./pages/assistants";
import { ForgotPassword, Login, Signup, TokenAction } from "./pages/auth";
import { PlatformAudit, PlatformModels, PlatformTenants } from "./pages/platform";
import { ChannelsPage } from "./features/channels/ChannelsPage";
import { ConversationDetail, ConversationsPage } from "./features/conversations/ConversationsPage";
import { InsightsPage } from "./features/insights/InsightsPage";
import { Dashboard, KnowledgeBaseDetail, KnowledgeBases, Members, NotVerified, Settings } from "./pages/tenant";
import { Link, match, navigate, useLocation } from "./router";

const PUBLIC = ["/login", "/signup", "/verify-email", "/reset-password", "/forgot-password", "/accept-invite"];

export function App() {
  const { path, query } = useLocation();
  const [me, setMeState] = useState<Me | null>(null);
  const [checked, setChecked] = useState(false);

  const setMe = useCallback((m: Me | null) => {
    setMeState(m);
    if (m) setCsrf(m.csrf_token);
  }, []);
  const refresh = useCallback(async () => {
    try {
      setMe(await get<Me>("/api/admin/auth/me"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setMe(null);
    } finally {
      setChecked(true);
    }
  }, [setMe]);
  useEffect(() => void refresh(), [refresh]);

  if (!checked) return <div className="loading">Loading…</div>;
  const token = query.get("token") ?? "";
  if (path === "/signup") return <Signup />;
  if (path === "/verify-email") return <TokenAction kind="verify" token={token} onDone={() => void refresh()} />;
  if (path === "/reset-password") return <TokenAction kind="reset" token={token} onDone={() => void refresh()} />;
  if (path === "/accept-invite") return <TokenAction kind="invite" token={token} onDone={(m) => m && setMe(m)} />;
  if (path === "/forgot-password") return <ForgotPassword />;
  if (!me || path === "/login") {
    if (!PUBLIC.includes(path) && path !== "/") window.history.replaceState({}, "", "/login");
    return <Login onLogin={setMe} />;
  }

  const tenant = me.current_tenant;
  const canEdit = tenant?.role === "admin";
  const isPlatform = path.startsWith("/platform");
  const page = route(path, { me, canEdit, refresh });

  return (
    <div className="shell">
      <aside>
        <div className="brand">Codura Docs</div>
        {me.tenants.length > 0 && (
          <select className="tenant-switch" value={tenant?.id ?? ""} onChange={async (e) => {
            setMe(await post<Me>("/api/admin/auth/switch-tenant", { tenant_id: e.target.value }));
            navigate("/");
          }}>
            {me.tenants.map((t) => <option key={t.id} value={t.id}>{t.name} ({t.role === "admin" ? "Admin" : "User"})</option>)}
          </select>
        )}
        {tenant && (
          <nav>
            <Link to="/">Overview</Link>
            <Link to="/knowledge-bases">Knowledge Bases</Link>
            <Link to="/assistants">Assistants & Playground</Link>
            {canEdit && <Link to="/channels">Channels & keys</Link>}
            <Link to="/conversations">Conversations</Link>
            <Link to="/insights">Insights</Link>
            <Link to="/members">Users</Link>
            <Link to="/settings">Settings</Link>
          </nav>
        )}
        {me.user.is_platform_admin && (
          <nav>
            <div className="nav-title">Platform</div>
            <Link to="/platform/tenants">Tenants</Link>
            <Link to="/platform/models">Model Profiles</Link>
            <Link to="/platform/audit">Admins & Audit</Link>
          </nav>
        )}
        <div className="who">
          <div>{me.user.email}</div>
          <button className="link" onClick={async () => { await post("/api/admin/auth/logout"); setMe(null); navigate("/login"); }}>Sign out</button>
        </div>
      </aside>
      <main>
        {tenant && tenant.status === "pending_verification" && !isPlatform && <NotVerified email={me.user.email} />}
        {!tenant && !isPlatform && (me.user.is_platform_admin
          ? <p>You are a Platform Admin. Open <Link to="/platform/tenants">Tenants</Link>.</p>
          : <p>You are not a member of any organisation yet.</p>)}
        {page}
      </main>
    </div>
  );
}

function route(path: string, ctx: { me: Me; canEdit: boolean; refresh: () => Promise<void> }) {
  const { me, canEdit } = ctx;
  if (path.startsWith("/platform")) {
    if (!me.user.is_platform_admin) return <p>Platform Admins only.</p>;
    if (path === "/platform/models") return <PlatformModels />;
    if (path === "/platform/audit") return <PlatformAudit />;
    return <PlatformTenants />;
  }
  if (!me.current_tenant) return null;
  let p: Record<string, string> | null;
  if ((p = match("/knowledge-bases/:id", path))) return <KnowledgeBaseDetail key={p.id} id={p.id!} canEdit={canEdit} />;
  if (path === "/knowledge-bases") return <KnowledgeBases canEdit={canEdit} />;
  if ((p = match("/assistants/:id", path))) return <AssistantDetail key={p.id} id={p.id!} canEdit={canEdit} />;
  if (path === "/assistants") return <Assistants canEdit={canEdit} />;
  if (path === "/members") return <Members canEdit={canEdit} myId={me.user.id} />;
  if (path === "/settings") return <Settings canEdit={canEdit} onChange={() => void ctx.refresh()} />;
  if ((path === "/channels" || path === "/keys") && canEdit) return <ChannelsPage tenantSlug={me.current_tenant.slug} />;
  if ((p = match("/conversations/:id", path))) return <ConversationDetail key={p.id} id={p.id!} />;
  if (path === "/conversations") return <ConversationsPage />;
  if (path === "/insights") return <InsightsPage canEdit={canEdit} />;
  return <Dashboard canEdit={canEdit} />;
}
