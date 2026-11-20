"use client";
import clsx from "clsx";
import { HeartPulse, Plus, RefreshCw, Terminal, Trash2 } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Async, Badge, Card, Empty, ErrorBox, Json, Modal, PageHeader, RiskBadge, SimulatedBadge, StatusBadge, useAction } from "@/components/ui";
import { api, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago, ms, pct } from "@/lib/format";

function example(schema: any): any {
  const out: any = {};
  for (const [k, v] of Object.entries<any>(schema?.properties || {})) {
    if (!(schema.required || []).includes(k)) {
      if (v.default != null) out[k] = v.default;
      continue;
    }
    out[k] = v.default ?? (v.type === "integer" || v.type === "number" ? 1 : v.type === "boolean" ? false : v.type === "array" ? [] : v.type === "object" ? {} : "");
  }
  return out;
}

function ToolConsole({ server, tool, onClose }: { server: string; tool: any; onClose: () => void }) {
  const [args, setArgs] = useState(JSON.stringify(example(tool.input_schema), null, 2));
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const invoke = async () => {
    setErr(null);
    setBusy(true);
    try {
      setResult(await post(`/api/mcp/servers/${server}/tools/${tool.name}/invoke`, { args: JSON.parse(args) }));
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal wide title={`Tool console — ${server}.${tool.name}`} onClose={onClose}>
      <div className="space-y-3 text-sm">
        <p className="text-muted">{tool.description}</p>
        <div className="flex flex-wrap gap-2"><RiskBadge risk={tool.risk} /><Badge>{tool.is_write ? "write" : "read-only"}</Badge><Badge>policy: {tool.policy_reason}</Badge></div>
        <div>
          <label className="label" htmlFor="args">Arguments (validated against the tool&apos;s JSON Schema on the server)</label>
          <textarea id="args" className="input min-h-[8rem] font-mono text-xs" value={args} onChange={(e) => setArgs(e.target.value)} />
        </div>
        <details><summary className="cursor-pointer text-xs text-muted">Input schema</summary><Json value={tool.input_schema} /></details>
        <button className="btn btn-primary" disabled={busy} onClick={invoke}><Terminal className="h-4 w-4" /> {busy ? "Invoking…" : "Invoke through gateway"}</button>
        {!!err && <ErrorBox error={err} />}
        {result && (
          <div data-testid="tool-result">
            <div className="mb-1 flex items-center gap-2"><StatusBadge status={result.status} />{result.latency_ms != null && <span className="text-xs text-muted">{ms(result.latency_ms)}</span>}{result.reason && <span className="text-xs text-muted">{result.reason}</span>}</div>
            {result.status === "approval_required" && <p className="mb-1 text-xs text-warn">Write tools cannot be executed from the console. They run only inside a workflow after a human approval.</p>}
            <Json value={result.result ?? result} />
          </div>
        )}
      </div>
    </Modal>
  );
}

function Register({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ id: "", name: "", url: "", description: "", credential_env: "" });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await post("/api/mcp/servers", { ...f, credential_env: f.credential_env || null });
      onDone();
      onClose();
    } catch (x) {
      setErr(x);
    } finally {
      setBusy(false);
    }
  };
  const field = (k: keyof typeof f, label: string, props: any = {}) => (
    <div><label className="label" htmlFor={`f-${k}`}>{label}</label><input id={`f-${k}`} className="input" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} {...props} /></div>
  );
  return (
    <Modal title="Register a remote MCP server" onClose={onClose}>
      <form className="space-y-3" onSubmit={submit}>
        {field("id", "Identifier", { required: true, pattern: "[a-z][a-z0-9_-]{1,30}", placeholder: "acme-crm" })}
        {field("name", "Display name", { required: true, placeholder: "Acme CRM" })}
        {field("url", "Streamable HTTP endpoint", { required: true, type: "url", placeholder: "https://mcp.example.com/mcp" })}
        {field("description", "Description")}
        {field("credential_env", "Credential environment variable (name only)", { placeholder: "ACME_MCP_TOKEN", pattern: "[A-Z][A-Z0-9_]*" })}
        <p className="text-xs text-muted">Secrets are never entered here. The backend reads the token from the named environment variable and sends it as a bearer header to this server only.</p>
        {!!err && <ErrorBox error={err} />}
        <button className="btn btn-primary" disabled={busy}>{busy ? "Connecting…" : "Register and connect"}</button>
      </form>
    </Modal>
  );
}

function ServerDetail({ id, onChange }: { id: string; onChange: () => void }) {
  const { can } = useAuth();
  const { data: s, error, mutate } = useSWR<any>(`/api/mcp/servers/${id}`);
  const { busy, run } = useAction();
  const [tool, setTool] = useState<any>(null);
  const admin = can("connector:admin");
  if (error) return <ErrorBox error={error} />;
  if (!s) return <Card><div className="p-6 text-sm text-muted">Loading server…</div></Card>;
  const after = () => { mutate(); onChange(); };
  return (
    <div className="space-y-4" data-testid="server-detail">
      <Card title={s.name} action={<div className="flex items-center gap-2">{s.kind === "demo" && <SimulatedBadge />}<StatusBadge status={s.status} /></div>}>
        <p className="text-sm text-muted">{s.description}</p>
        {s.kind === "demo" && <p className="mt-1 text-xs text-muted">This is a local MCP server speaking the real protocol over stdio, backed by synthetic records. It is not connected to a live {s.provider} tenant.</p>}
        {s.last_error && (s.status === "not_configured" ? <p className="mt-2 text-xs text-muted">Not connected: {s.last_error}.</p> : <div className="mt-2"><ErrorBox error={s.last_error} /></div>)}
        <dl className="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 xl:grid-cols-4">
          <div><dt className="label">Transport</dt><dd className="font-mono text-xs">{s.transport}</dd></div>
          <div><dt className="label">Server</dt><dd className="font-mono text-xs">{s.server_info?.name ? `${s.server_info.name} · MCP ${s.server_info.protocol}` : "—"}</dd></div>
          <div><dt className="label">Credential</dt><dd className="text-xs">{s.credential.required ? <>env <span className="font-mono">{s.credential.env_var}</span> · {s.credential.configured ? "set" : "not set"}</> : "none required"}</dd></div>
          <div><dt className="label">Permission scopes</dt><dd className="flex flex-wrap gap-1">{s.scopes.length ? s.scopes.map((x: string) => <Badge key={x}>{x}</Badge>) : "—"}</dd></div>
          <div><dt className="label">Calls</dt><dd className="tabular-nums">{s.stats?.calls ?? 0} · {s.stats?.calls ? pct(s.stats.ok / s.stats.calls) : "—"} ok</dd></div>
          <div><dt className="label">Avg latency</dt><dd className="tabular-nums">{ms(s.stats?.avg_latency_ms)}</dd></div>
          <div><dt className="label">Last health check</dt><dd className="text-xs">{s.last_checked ? `${ago(s.last_checked)} · ${ms(s.last_ping_ms)}` : "not run yet"}</dd></div>
          <div><dt className="label">Policy-governed calls</dt><dd className="tabular-nums">{s.stats?.governed ?? 0}</dd></div>
        </dl>
        <div className="mt-4 flex flex-wrap gap-2">
          <button className="btn" disabled={!!busy} onClick={() => run("health", async () => { const h = await post(`/api/mcp/servers/${id}/health`); after(); if (!h.ok) throw new Error(h.error || "health check failed"); }, "Health check passed")}><HeartPulse className="h-4 w-4" /> Health check</button>
          {admin && <button className="btn" disabled={!!busy} onClick={() => run("reconnect", async () => { await post(`/api/mcp/servers/${id}/reconnect`); after(); }, "Reconnected and re-discovered tools")}><RefreshCw className="h-4 w-4" /> Reconnect</button>}
          {admin && !s.builtin && <button className="btn btn-danger" disabled={!!busy} onClick={() => run("remove", async () => { await api(`/api/mcp/servers/${id}`, { method: "DELETE" }); onChange(); }, "Server removed")}><Trash2 className="h-4 w-4" /> Remove</button>}
        </div>
      </Card>

      <Card title={`Tools discovered over MCP (${s.tools.length})`} pad={false}>
        {s.tools.length ? (
          <div className="overflow-x-auto"><table className="w-full min-w-[640px]">
            <thead><tr><th className="th">Tool</th><th className="th">Access</th><th className="th">Risk</th><th className="th">Policy</th><th className="th">Roles</th><th className="th">Enabled</th><th className="th"></th></tr></thead>
            <tbody className="divide-y divide-line">
              {s.tools.map((t: any) => (
                <tr key={t.name}>
                  <td className="td"><div className="font-mono text-xs font-medium">{t.name}</div><div className="line-clamp-1 max-w-xs text-xs text-muted">{t.description}</div></td>
                  <td className="td text-xs">{t.is_write ? "write" : "read"}</td>
                  <td className="td"><RiskBadge risk={t.risk} /></td>
                  <td className="td text-xs">{t.is_write ? "approval required" : "allowed"}</td>
                  <td className="td text-xs">{t.roles.join(", ")}</td>
                  <td className="td">
                    <input type="checkbox" aria-label={`Enable ${t.name}`} checked={t.enabled} disabled={!admin || !!busy}
                      onChange={(e) => run("toggle", async () => { await api(`/api/mcp/servers/${id}/tools/${t.name}`, { method: "PUT", json: { enabled: e.target.checked } }); mutate(); }, `${t.name} ${e.target.checked ? "enabled" : "disabled"} for this workspace`)} />
                  </td>
                  <td className="td text-right"><button className="btn" onClick={() => setTool(t)}>Try</button></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        ) : <Empty title="No tools discovered" hint={s.status === "not_configured" ? "Set the credential environment variable and reconnect." : "The server is not connected."} />}
      </Card>

      <Card title="Recent invocations" pad={false}>
        {s.recent?.length ? (
          <table className="w-full"><tbody className="divide-y divide-line">
            {s.recent.map((r: any) => (
              <tr key={r.id}><td className="td font-mono text-xs">{r.tool}</td><td className="td"><StatusBadge status={r.status} /></td><td className="td text-xs text-muted">{r.error || r.decision}</td><td className="td tabular-nums">{ms(r.latency_ms)}</td><td className="td text-xs text-muted">{ago(r.created_at)}</td></tr>
            ))}
          </tbody></table>
        ) : <Empty title="No invocations yet" />}
      </Card>
      {tool && <ToolConsole server={id} tool={tool} onClose={() => { setTool(null); mutate(); }} />}
    </div>
  );
}

export default function Integrations() {
  const { can } = useAuth();
  const { data, error, mutate } = useSWR<any[]>("/api/mcp/servers", { refreshInterval: 8000 });
  const [selected, setSelected] = useState<string | null>(null);
  const [registering, setRegistering] = useState(false);
  const current = selected && data?.some((s) => s.id === selected) ? selected : (data?.find((s) => s.status === "connected") || data?.[0])?.id;
  return (
    <>
      <PageHeader title="MCP Integration Hub" subtitle="Model Context Protocol servers connected through the official SDK. Every call goes through one gateway that validates the schema, applies policy and role checks, retries, times out and records telemetry.">
        {can("connector:admin") && <button className="btn btn-primary" onClick={() => setRegistering(true)}><Plus className="h-4 w-4" /> Register server</button>}
      </PageHeader>
      <Async data={data} error={error} retry={() => mutate()}>
        {(servers) => (
          <div className="grid gap-4 lg:grid-cols-[19rem_minmax(0,1fr)]">
            <ul className="space-y-2" aria-label="MCP servers">
              {[...servers].sort((a, b) => Number(b.status === "connected") - Number(a.status === "connected")).map((s) => (
                <li key={s.id}>
                  <button onClick={() => setSelected(s.id)} aria-current={current === s.id} className={clsx("card w-full p-3 text-left hover:bg-raised", current === s.id && "!border-accent")}>
                    <div className="flex items-center justify-between gap-2"><span className="text-sm font-semibold">{s.name}</span><StatusBadge status={s.status} /></div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">{s.kind === "demo" ? <SimulatedBadge /> : <Badge>remote</Badge>}<span>{s.transport}</span><span>· {s.tool_count} tools</span></div>
                  </button>
                </li>
              ))}
            </ul>
            {current ? <ServerDetail key={current} id={current} onChange={() => mutate()} /> : <Card><Empty title="No MCP servers registered" /></Card>}
          </div>
        )}
      </Async>
      {registering && <Register onClose={() => setRegistering(false)} onDone={() => mutate()} />}
    </>
  );
}
