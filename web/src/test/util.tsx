import { render } from "@testing-library/react";
import { SWRConfig } from "swr";
import { vi } from "vitest";
import { ToastProvider } from "@/components/ui";
import { fetcher } from "@/lib/api";
import { AuthProvider } from "@/lib/auth";

export const USER = { id: "u1", email: "op@x", name: "Op", role: "operator", workspace_id: "w", workspace_name: "W", scopes: ["workiq:read", "workiq:write", "agent:run", "knowledge:write", "eval:run"] };

type Route = { status?: number; body: unknown } | ((init: RequestInit | undefined) => { status?: number; body: unknown });

/** Mocks fetch with a "METHOD /path" → response table and records every call. */
export function mockApi(routes: Record<string, Route>) {
  const calls: { key: string; body: any }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method || "GET"} ${String(input).split("?")[0]}`;
    calls.push({ key, body: typeof init?.body === "string" ? JSON.parse(init.body) : init?.body });
    const hit = routes[key];
    const r = typeof hit === "function" ? hit(init) : hit ?? { status: 404, body: { detail: `no mock for ${key}` } };
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

export function renderApp(ui: React.ReactElement) {
  return render(
    <SWRConfig value={{ fetcher, provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
      <AuthProvider><ToastProvider>{ui}</ToastProvider></AuthProvider>
    </SWRConfig>,
  );
}
