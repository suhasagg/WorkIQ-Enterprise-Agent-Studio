"use client";
import clsx from "clsx";
import { Activity, BookOpen, Bot, ClipboardCheck, FlaskConical, LayoutDashboard, LogOut, Menu, Moon, Plug, Sun, Workflow, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { useAuth } from "@/lib/auth";
import { Login } from "./login";
import { Badge, Spinner } from "./ui";

export const NAV = [
  { href: "/", label: "Command Center", icon: LayoutDashboard },
  { href: "/studio", label: "Workflow Studio", icon: Workflow },
  { href: "/chat", label: "Agent Chat", icon: Bot },
  { href: "/approvals", label: "Approvals", icon: ClipboardCheck },
  { href: "/integrations", label: "MCP Integrations", icon: Plug },
  { href: "/knowledge", label: "Knowledge", icon: BookOpen },
  { href: "/observability", label: "Observability", icon: Activity },
  { href: "/evals", label: "Evaluations", icon: FlaskConical },
];

export function ThemeToggle() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);
  const toggle = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
    setDark(next);
  };
  return (
    <button className="btn !px-2" onClick={toggle} aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}>
      {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </button>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { user, loading, logout } = useAuth();
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const { data: sys } = useSWR(user ? "/api/system" : null);
  const { data: pending } = useSWR<any[]>(user ? "/api/approvals?status=pending" : null, { refreshInterval: 5000 });
  useEffect(() => {
    setOpen(false);
  }, [path]);

  if (loading) return <div className="flex h-screen items-center justify-center"><Spinner label="Loading workspace" /></div>;
  if (!user) return <Login />;

  return (
    <div className="flex min-h-screen">
      <aside className={clsx("fixed inset-y-0 left-0 z-40 w-60 shrink-0 border-r border-line bg-surface transition-transform lg:static lg:translate-x-0", open ? "translate-x-0" : "-translate-x-full")}>
        <div className="flex h-14 items-center justify-between border-b border-line px-4">
          <Link href="/" className="flex items-center gap-2 font-semibold">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent text-xs font-bold text-white dark:text-slate-950">IQ</span>
            <span className="text-sm leading-tight">Work IQ<br /><span className="text-xs font-normal text-muted">Agent Studio</span></span>
          </Link>
          <button className="btn !p-1 lg:hidden" aria-label="Close navigation" onClick={() => setOpen(false)}><X className="h-4 w-4" /></button>
        </div>
        <nav aria-label="Primary" className="space-y-0.5 p-2">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link key={href} href={href} aria-current={active ? "page" : undefined}
                className={clsx("flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium", active ? "bg-accent/10 text-accent" : "text-muted hover:bg-raised hover:text-fg")}>
                <Icon className="h-4 w-4" aria-hidden />
                <span className="flex-1">{label}</span>
                {href === "/approvals" && !!pending?.length && <span className="rounded-full bg-warn/20 px-1.5 text-xs text-warn" aria-label={`${pending.length} pending`}>{pending.length}</span>}
              </Link>
            );
          })}
        </nav>
        {sys && (
          <div className="absolute inset-x-0 bottom-0 space-y-1 border-t border-line p-3 text-xs text-muted">
            <div className="flex items-center justify-between"><span>Model</span><span className="font-mono text-fg">{sys.model.model}</span></div>
            <div className="flex items-center justify-between"><span>Embeddings</span><span className="font-mono text-fg">{sys.embeddings.model}</span></div>
          </div>
        )}
      </aside>
      {open && <div className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={() => setOpen(false)} aria-hidden />}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-surface/90 px-4 backdrop-blur">
          <button className="btn !p-1.5 lg:hidden" aria-label="Open navigation" onClick={() => setOpen(true)}><Menu className="h-4 w-4" /></button>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">{user.workspace_name}</div>
          </div>
          {sys?.demo_mode && <span className="hidden sm:inline-flex"><Badge tone="accent" title={sys.notice}>Demo mode · synthetic data</Badge></span>}
          <ThemeToggle />
          <div className="hidden text-right text-xs sm:block">
            <div className="font-medium">{user.name}</div>
            <div className="capitalize text-muted">{user.role}</div>
          </div>
          <button className="btn !px-2" onClick={logout} aria-label="Sign out"><LogOut className="h-4 w-4" /></button>
        </header>
        <main className="mx-auto w-full max-w-[1500px] flex-1 p-4 sm:p-6">{children}</main>
      </div>
    </div>
  );
}
