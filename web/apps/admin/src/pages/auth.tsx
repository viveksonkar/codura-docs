import { useState } from "react";
import { type Me, post } from "../api";
import { Link, navigate } from "../router";
import { Alert, errorText } from "../ui";

function AuthCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="auth">
      <div className="auth-card">
        <div className="brand">Codura Docs</div>
        <h1>{title}</h1>
        {children}
      </div>
    </div>
  );
}

export function Login({ onLogin }: { onLogin: (me: Me) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <AuthCard title="Sign in">
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            onLogin(await post<Me>("/api/admin/auth/login", { email, password }));
            navigate("/");
          } catch (err) {
            setError(errorText(err));
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>Email<input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label>Password<input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <Alert>{error}</Alert>
        <button disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
      <p className="muted">
        New organisation? <Link to="/signup">Create an account</Link> · <Link to="/forgot-password">Forgot password?</Link>
      </p>
    </AuthCard>
  );
}

export function Signup() {
  const [form, setForm] = useState({ org_name: "", name: "", email: "", password: "" });
  const [done, setDone] = useState("");
  const [error, setError] = useState("");
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  return (
    <AuthCard title="Create your organisation">
      {done ? (
        <Alert kind="ok">{done}</Alert>
      ) : (
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              const r = await post<{ message: string }>("/api/auth/signup", form);
              setDone(r.message);
            } catch (err) {
              setError(errorText(err));
            }
          }}
        >
          <label>Organisation name<input required minLength={2} value={form.org_name} onChange={set("org_name")} /></label>
          <label>Your name<input value={form.name} onChange={set("name")} /></label>
          <label>Work email<input type="email" required value={form.email} onChange={set("email")} /></label>
          <label>Password (10+ characters)<input type="password" required minLength={10} value={form.password} onChange={set("password")} /></label>
          <Alert>{error}</Alert>
          <button>Create account</button>
        </form>
      )}
      <p className="muted">Already registered? <Link to="/login">Sign in</Link></p>
    </AuthCard>
  );
}

export function TokenAction({ kind, token, onDone }: { kind: "verify" | "reset" | "invite"; token: string; onDone: (me?: Me) => void }) {
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const titles = { verify: "Verify your email", reset: "Choose a new password", invite: "Join your team" };
  const submit = async () => {
    try {
      if (kind === "verify") {
        await post("/api/auth/verify-email", { token });
        setMsg({ kind: "ok", text: "Email verified. Your organisation is active — sign in to continue." });
      } else if (kind === "reset") {
        await post("/api/auth/reset-password", { token, password });
        setMsg({ kind: "ok", text: "Password changed. You can sign in now." });
      } else {
        onDone(await post<Me>("/api/auth/accept-invite", { token, name, password }));
        navigate("/");
      }
    } catch (err) {
      setMsg({ kind: "error", text: errorText(err) });
    }
  };
  return (
    <AuthCard title={titles[kind]}>
      {!token ? <Alert>Missing token in the link.</Alert> : null}
      {kind !== "verify" && (
        <>
          {kind === "invite" && <label>Your name<input value={name} onChange={(e) => setName(e.target.value)} /></label>}
          <label>
            {kind === "invite" ? "Password (new, or your existing one)" : "New password (10+ characters)"}
            <input type="password" minLength={10} value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
        </>
      )}
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
      <button disabled={!token} onClick={submit}>{kind === "verify" ? "Verify email" : "Continue"}</button>
      <p className="muted"><Link to="/login">Back to sign in</Link></p>
    </AuthCard>
  );
}

export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState("");
  return (
    <AuthCard title="Reset your password">
      <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
      {msg && <Alert kind="ok">{msg}</Alert>}
      <button
        onClick={async () => {
          const r = await post<{ message: string }>("/api/auth/forgot-password", { email });
          setMsg(r.message);
        }}
      >
        Send reset link
      </button>
      <p className="muted"><Link to="/login">Back to sign in</Link></p>
    </AuthCard>
  );
}
