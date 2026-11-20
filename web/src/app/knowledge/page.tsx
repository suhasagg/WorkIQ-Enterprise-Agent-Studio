"use client";
import { FileText, RefreshCw, Search, ShieldAlert, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import useSWR from "swr";
import { highlight } from "@/components/report";
import { Async, Badge, Card, Empty, ErrorBox, Modal, PageHeader, Spinner, Stat, StatusBadge, useAction } from "@/components/ui";
import { api, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago, ms, num } from "@/lib/format";

const ROLES = ["viewer", "operator", "approver", "admin"];

function UploadForm({ onDone }: { onDone: () => void }) {
  const file = useRef<HTMLInputElement>(null);
  const [strategy, setStrategy] = useState("auto");
  const [tags, setTags] = useState("");
  const [roles, setRoles] = useState<string[]>([]);
  const [name, setName] = useState("");
  const { busy, run } = useAction();
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const f = file.current?.files?.[0];
    if (!f) return;
    run("upload", async () => {
      const body = new FormData();
      body.append("file", f);
      body.append("chunk_strategy", strategy);
      body.append("tags", tags);
      body.append("allowed_roles", roles.join(","));
      const res = await api("/api/documents", { method: "POST", body });
      if (file.current) file.current.value = "";
      setName("");
      onDone();
      if (res.duplicate) throw new Error("This exact file is already indexed; no new copy was created.");
    }, "Upload accepted — parsing, chunking and embedding in the background");
  };
  return (
    <form className="space-y-3" onSubmit={submit} data-testid="upload-form">
      <div>
        <label className="label" htmlFor="file">Document (PDF, Markdown or text, up to 15 MB)</label>
        <input id="file" ref={file} type="file" accept=".pdf,.md,.markdown,.txt" required className="input file:mr-3 file:rounded file:border-0 file:bg-raised file:px-2 file:py-1 file:text-xs" onChange={(e) => setName(e.target.files?.[0]?.name || "")} />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="strategy">Chunking</label>
          <select id="strategy" className="input" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="auto">Auto (headings when present)</option><option value="heading">By heading</option><option value="window">Sliding window with overlap</option>
          </select>
        </div>
        <div><label className="label" htmlFor="tags">Tags (comma separated)</label><input id="tags" className="input" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="policy, support" /></div>
      </div>
      <fieldset>
        <legend className="label">Restrict to roles (none selected = everyone in the workspace)</legend>
        <div className="flex flex-wrap gap-3 text-sm">
          {ROLES.map((r) => <label key={r} className="flex items-center gap-1.5 capitalize"><input type="checkbox" checked={roles.includes(r)} onChange={(e) => setRoles(e.target.checked ? [...roles, r] : roles.filter((x) => x !== r))} />{r}</label>)}
        </div>
      </fieldset>
      <button className="btn btn-primary" disabled={!!busy || !name}><Upload className="h-4 w-4" /> {busy ? "Uploading…" : "Upload and index"}</button>
    </form>
  );
}

function DocModal({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: d, error } = useSWR<any>(`/api/documents/${id}`);
  return (
    <Modal wide title={d?.title || "Document"} onClose={onClose}>
      <Async data={d} error={error}>
        {(doc) => (
          <div className="space-y-3 text-sm">
            <div className="flex flex-wrap gap-2 text-xs">
              <Badge>{doc.filename}</Badge><Badge>{doc.source_type}</Badge><Badge>{doc.chunk_count} chunks · {doc.metadata?.chunk_strategy_used || doc.chunk_strategy}</Badge><Badge>{num(doc.metadata?.word_count)} words</Badge><Badge>{doc.embedding_model}</Badge>
              {doc.allowed_roles.length > 0 && <Badge tone="warn">Restricted: {doc.allowed_roles.join(", ")}</Badge>}
              {doc.untrusted_flags.length > 0 && <Badge tone="bad">Untrusted content: {doc.untrusted_flags.join(", ")}</Badge>}
            </div>
            {doc.error && <ErrorBox error={doc.error} />}
            <ol className="max-h-[28rem] space-y-2 overflow-auto">
              {doc.chunks.map((c: any) => (
                <li key={c.id} className="rounded-lg border border-line p-2.5">
                  <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted"><span className="font-mono">#{c.ordinal}</span>{c.heading && <span>§ {c.heading}</span>}{c.page != null && <span>p. {c.page}</span>}<span>{c.token_count} tokens</span>{c.flags.length > 0 && <Badge tone="bad">quarantined instructions</Badge>}</div>
                  <p className="whitespace-pre-wrap text-xs leading-relaxed">{c.content}</p>
                </li>
              ))}
            </ol>
          </div>
        )}
      </Async>
    </Modal>
  );
}

function SearchPanel() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("hybrid");
  const [rerank, setRerank] = useState(true);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const search = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      setRes(await post("/api/knowledge/search", { query, mode, rerank, k: 6 }));
    } catch (x) {
      setErr(x);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Retrieval playground">
      <form className="flex flex-wrap items-end gap-2" onSubmit={search}>
        <div className="min-w-[14rem] flex-1"><label className="label" htmlFor="q">Query</label><input id="q" className="input" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. SSO login failure workaround" required minLength={2} /></div>
        <div><label className="label" htmlFor="mode">Mode</label><select id="mode" className="input" value={mode} onChange={(e) => setMode(e.target.value)}><option value="hybrid">Hybrid (vector + keyword, RRF)</option><option value="vector">Vector only (pgvector)</option><option value="keyword">Keyword only (full-text)</option></select></div>
        <label className="flex items-center gap-1.5 pb-2 text-sm"><input type="checkbox" checked={rerank} onChange={(e) => setRerank(e.target.checked)} /> Rerank</label>
        <button className="btn btn-primary" disabled={busy}><Search className="h-4 w-4" /> {busy ? "Searching…" : "Search"}</button>
      </form>
      <div className="mt-4" aria-live="polite">
        {!!err && <ErrorBox error={err} />}
        {busy && <Spinner label="Searching" />}
        {!busy && res && (res.results.length ? (
          <>
            <p className="mb-2 text-xs text-muted">{res.results.length} of {res.candidates} candidates · {ms(res.latency_ms)} · {res.mode}{res.reranked ? " + rerank" : ""} · only documents your role may read</p>
            <ol className="space-y-2" data-testid="search-results">
              {res.results.map((r: any, i: number) => (
                <li key={r.id} className="rounded-lg border border-line p-3">
                  <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
                    <span className="font-mono text-xs text-accent">{i + 1}</span><span className="font-medium">{r.title}</span>{r.heading && <span className="text-xs text-muted">§ {r.heading}</span>}{r.page != null && <span className="text-xs text-muted">p. {r.page}</span>}
                    {r.untrusted && <Badge tone="bad"><ShieldAlert className="h-3 w-3" /> contains instructions — treated as data</Badge>}
                    <span className="ml-auto font-mono text-xs text-muted">score {r.score.toFixed(3)}</span>
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-muted">{highlight(r.content, r.highlights)}</p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5 text-[11px] text-muted">
                    {r.vector_rank != null && <Badge>vector #{r.vector_rank} ({r.vector_score?.toFixed(2)})</Badge>}
                    {r.keyword_rank != null && <Badge>keyword #{r.keyword_rank}</Badge>}
                    {r.rerank_score != null && <Badge>rerank {r.rerank_score.toFixed(2)}</Badge>}
                  </div>
                </li>
              ))}
            </ol>
          </>
        ) : <Empty title="No matching passages" hint="Nothing relevant in the documents your role can read." />)}
      </div>
    </Card>
  );
}

export default function Knowledge() {
  const { can } = useAuth();
  const { data: stats } = useSWR<any>("/api/knowledge/stats", { refreshInterval: 3000 });
  const { data: docs, error, mutate } = useSWR<any[]>("/api/documents", { refreshInterval: (d: any) => (d?.some((x: any) => !["indexed", "failed"].includes(x.status)) ? 800 : 5000) });
  const { busy, run } = useAction();
  const [open, setOpen] = useState<string | null>(null);
  const write = can("knowledge:write");
  return (
    <>
      <PageHeader title="Enterprise Knowledge Studio" subtitle="Upload documents, watch them get parsed, chunked and embedded into PostgreSQL + pgvector, and test hybrid retrieval with role-based filtering." />
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Stat label="Documents" value={num(stats?.documents)} sub={`${stats?.processing ?? 0} processing · ${stats?.failed ?? 0} failed`} />
          <Stat label="Chunks" value={num(stats?.chunks)} />
          <Stat label="Restricted" value={num(stats?.restricted)} sub="role-limited documents you can see" />
          <Stat label="Flagged" value={num(stats?.flagged)} sub="contain embedded instructions" tone={stats?.flagged ? "warn" : undefined} />
          <Stat label="Embeddings" value={<span className="text-base">{stats?.embedding_model ?? "—"}</span>} sub={stats ? `${stats.embedding_dim} dims · ${stats.vector_store}` : undefined} />
        </div>
        <div className="grid gap-4 xl:grid-cols-[24rem_minmax(0,1fr)]">
          <Card title="Upload">{write ? <UploadForm onDone={() => mutate()} /> : <p className="text-sm text-muted">Your role is read-only. Sign in as an operator or admin to upload.</p>}</Card>
          <SearchPanel />
        </div>
        <Card title="Documents" pad={false}>
          <Async data={docs} error={error} retry={() => mutate()} empty={<Empty title="No documents yet" hint="Upload a PDF, Markdown or text file to make it searchable." />}>
            {(ds) => (
              <div className="overflow-x-auto"><table className="w-full min-w-[720px]" data-testid="documents">
                <thead><tr><th className="th">Document</th><th className="th">Type</th><th className="th">Status</th><th className="th">Chunks</th><th className="th">Access</th><th className="th">Added</th><th className="th"></th></tr></thead>
                <tbody className="divide-y divide-line">
                  {ds.map((d) => (
                    <tr key={d.id} className="hover:bg-raised">
                      <td className="td"><button className="flex items-center gap-2 text-left hover:underline" onClick={() => setOpen(d.id)}><FileText className="h-4 w-4 shrink-0 text-muted" aria-hidden /><span><span className="font-medium">{d.title}</span><span className="block font-mono text-xs text-muted">{d.filename}</span></span></button></td>
                      <td className="td text-xs">{d.source_type}</td>
                      <td className="td">
                        <StatusBadge status={d.status} />
                        {!["indexed", "failed"].includes(d.status) && <div className="mt-1 h-1 w-24 overflow-hidden rounded bg-raised" role="progressbar" aria-valuenow={d.progress} aria-valuemin={0} aria-valuemax={100} aria-label={`Indexing ${d.title}`}><div className="h-full bg-info transition-all" style={{ width: `${d.progress}%` }} /></div>}
                        {d.status !== "indexed" && <div className="text-xs text-muted">{d.error || d.stage}</div>}
                      </td>
                      <td className="td tabular-nums">{d.chunk_count}</td>
                      <td className="td">{d.allowed_roles.length ? <Badge tone="warn">{d.allowed_roles.join(", ")}</Badge> : <span className="text-xs text-muted">workspace</span>}{d.untrusted_flags.length > 0 && <Badge tone="bad">untrusted</Badge>}</td>
                      <td className="td text-xs text-muted">{ago(d.created_at)}{d.origin === "seed" ? " · demo seed" : ""}</td>
                      <td className="td text-right">
                        {write && <span className="inline-flex gap-1">
                          <button className="btn !p-1.5" aria-label={`Re-index ${d.title}`} disabled={!!busy} onClick={() => run("reindex", async () => { await post(`/api/documents/${d.id}/reindex`); mutate(); }, "Re-indexing started")}><RefreshCw className="h-3.5 w-3.5" /></button>
                          <button className="btn btn-danger !p-1.5" aria-label={`Delete ${d.title}`} disabled={!!busy} onClick={() => run("delete", async () => { await api(`/api/documents/${d.id}`, { method: "DELETE" }); mutate(); }, "Document and its chunks deleted")}><Trash2 className="h-3.5 w-3.5" /></button>
                        </span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            )}
          </Async>
        </Card>
      </div>
      {open && <DocModal id={open} onClose={() => setOpen(null)} />}
    </>
  );
}
