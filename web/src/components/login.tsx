"use client";
import { useState } from "react";
import useSWR from "swr";
import { useAuth } from "@/lib/auth";
import { ThemeToggle } from "./shell";
import { ErrorBox } from "./ui";

export function Login() {
  const { login } = useAuth();
  const { data: demo } = useSWR("/api/auth/demo-users");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: string, p: string) => {
    setBusy(true);
    setError(null);
    try {
      await login(e, p);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="absolute right-4 top-4"><ThemeToggle /></div>
      <div className="w-full max-w-md space-y-4">
        <div className="text-center">
          <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-accent font-bold text-white dark:text-slate-950">IQ</div>
          <h1 className="text-xl font-semibold">Work IQ Enterprise Agent Studio</h1>
          <p className="mt-1 text-sm text-muted">LangGraph and MCP multi-agent platform</p>
        </div>
        <form className="card space-y-3 p-5" onSubmit={(e) => { e.preventDefault(); submit(email, password); }}>
          <div>
            <label className="label" htmlFor="email">Email</label>
            <input id="email" className="input" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="password">Password</label>
            <input id="password" className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          {!!error && <ErrorBox error={error} />}
          <button className="btn btn-primary w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
        {demo?.demo_mode && (
          <div className="card p-4">
            <div className="mb-2 text-xs font-medium text-muted">Demo accounts (synthetic workspaces) — click to sign in</div>
            <div className="space-y-1.5">
              {demo.users.map((u: any) => (
                <button key={u.email} className="btn w-full !justify-between" disabled={busy} onClick={() => submit(u.email, demo.password_hint)}>
                  <span>{u.name} <span className="text-muted">· {u.workspace}</span></span>
                  <span className="capitalize text-muted">{u.role}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
