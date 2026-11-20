export const ms = (v?: number | null) => (v == null ? "—" : v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(1)} s`);
export const pct = (v?: number | null, digits = 0) => (v == null ? "—" : `${(v * 100).toFixed(digits)}%`);
export const num = (v?: number | null) => (v == null ? "—" : new Intl.NumberFormat("en-US").format(v));
export const usd = (v?: number | null) => (v == null ? "—" : `$${v.toFixed(v > 0 && v < 1 ? 4 : 2)}`);
const LABELS: Record<string, string> = { mrr: "MRR", ndcg_at_k: "NDCG@k", recall_at_k: "Recall@k", nps: "NPS" };
export const title = (s?: string | null) => LABELS[s || ""] ?? (s || "").replace(/[_.]/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export function ago(ts?: string | null): string {
  if (!ts) return "—";
  const s = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export const clock = (ts?: string | null) =>
  ts ? new Date(ts).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
