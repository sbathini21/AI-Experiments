"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Me = { user: { id: string; email: string } | null };

export default function Login() {
  const [me, setMe] = useState<Me["user"] | undefined>(undefined);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api<Me>("/api/auth/me").then((d) => setMe(d.user)).catch(() => setMe(null)); }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      const u = await api<{ id: string; email: string }>(`/api/auth/${mode}`, { method: "POST", json: { email, password } });
      setMe(u);
    } catch (e: any) {
      setErr(e.message);
    }
  }

  if (me === undefined) return <p className="muted">Loading…</p>;
  if (me)
    return (
      <main className="card stack" style={{ maxWidth: 440, margin: "40px auto" }}>
        <p className="section-title">Account</p>
        <p style={{ margin: 0 }}>Signed in as <b>{me.email}</b></p>
        <button className="btn" onClick={async () => { await api("/api/auth/logout", { method: "POST" }); setMe(null); }}>Sign out</button>
      </main>
    );
  return (
    <main className="card" style={{ maxWidth: 440, margin: "40px auto" }}>
      <p className="section-title">{mode === "login" ? "Sign in" : "Create account"}</p>
      <p className="small muted">Only an email and password — no brokerage or bank details, ever.</p>
      <form className="stack" onSubmit={submit}>
        <label className="field">Email<input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" /></label>
        <label className="field">Password<input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} /></label>
        {err && <div className="notice">{err}</div>}
        <button className="btn btn-primary">{mode === "login" ? "Sign in" : "Create account"}</button>
      </form>
      <p className="small">
        {mode === "login" ? "New here? " : "Have an account? "}
        <a href="#" onClick={(e) => { e.preventDefault(); setMode(mode === "login" ? "signup" : "login"); }}>{mode === "login" ? "Create an account" : "Sign in"}</a>
      </p>
    </main>
  );
}
