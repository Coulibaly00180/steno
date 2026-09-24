"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { Icon } from "./Icons";
import { serviceLabels, useSystemStatus, type ServiceName, type ServiceState } from "../lib/status";

const nav = [
  { href: "/", label: "Accueil", icon: "home" as const },
  { href: "/record", label: "Enregistrer", icon: "mic" as const },
  { href: "/library", label: "Bibliothèque", icon: "library" as const },
  { href: "/ask", label: "Questions", icon: "chat" as const },
  { href: "/actions", label: "Actions", icon: "check" as const },
  { href: "/entities", label: "Personnes et dates", icon: "people" as const },
  { href: "/templates", label: "Templates", icon: "template" as const },
  { href: "/models", label: "Modèles", icon: "chip" as const },
];

export default function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [systemOpen, setSystemOpen] = useState(false);
  const { status, reachable } = useSystemStatus();

  useEffect(() => {
    const saved = localStorage.getItem("video-ai-theme");
    const initial = saved === "dark" || saved === "light" ? saved : "light";
    setTheme(initial);
    document.documentElement.dataset.theme = initial;
  }, []);

  function chooseTheme(next: "light" | "dark") {
    setTheme(next);
    localStorage.setItem("video-ai-theme", next);
    document.documentElement.dataset.theme = next;
  }

  const stateClass = (state: ServiceState) => state === "ok" ? "service-ok" : state === "degraded" ? "service-warn" : "service-bad";
  const rows: { name: string; className: string; label: string }[] = [
    { name: "API", className: reachable === false ? "service-bad" : "service-ok", label: reachable === null ? "Vérification" : reachable ? "Opérationnel" : "Injoignable" },
    ...(Object.keys(serviceLabels) as ServiceName[]).map(name => {
      const service = status?.services[name];
      if (!service) return { name: serviceLabels[name], className: reachable === false ? "service-bad" : "service-ok", label: reachable === false ? "Inconnu" : "Vérification" };
      return { name: serviceLabels[name], className: stateClass(service.status), label: service.detail || (service.status === "ok" ? "Opérationnel" : "Indisponible") };
    }),
  ];
  // Red when the API itself is unreachable or a core dependency is down; orange for anything else not ok.
  const dotClass = reachable === null ? "pending" : reachable === false || status?.overall === "down" ? "bad" : status?.overall === "degraded" ? "warn" : "";

  return <div className="app-shell">
    <aside className="sidebar">
      <Link href="/" className="brand-lockup" aria-label="Sténo, accueil"><span className="brand-mark"><i /></span><span className="brand-name">Sténo</span></Link>
      <nav className="sidebar-nav" aria-label="Navigation principale">
        {nav.map(item => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return <Link key={item.href} href={item.href} className={`nav-item${active ? " active" : ""}`} aria-label={item.label} title={item.label}><Icon name={item.icon}/><span className="nav-label">{item.label}</span></Link>;
        })}
      </nav>
      <div className="sidebar-spacer" />
      <div className="system-wrap">
        {systemOpen && <div className="system-popover"><div className="eyebrow">État du système</div>{rows.map(row => <div className="service-row" key={row.name}><span>{row.name}</span><span className={row.className}><i /><span className="service-detail" title={row.label}>{row.label}</span></span></div>)}</div>}
        <button className="nav-item nav-button" onClick={() => setSystemOpen(value => !value)} aria-expanded={systemOpen}><Icon name="system"/>État du système<span className={`health-dot ${dotClass}`} /></button>
      </div>
      <Link href="/settings" className={`nav-item${pathname.startsWith("/settings") ? " active" : ""}`} aria-label="Paramètres" title="Paramètres"><Icon name="settings"/><span className="nav-label">Paramètres</span></Link>
      <div className="privacy-note"><Icon name="shield" size={13}/><span>IA locale — tout reste sur cet appareil</span></div>
      <div className="theme-switch" aria-label="Thème"><button className={theme === "light" ? "selected" : ""} onClick={() => chooseTheme("light")}><Icon name="sun" size={13}/>Clair</button><button className={theme === "dark" ? "selected" : ""} onClick={() => chooseTheme("dark")}><Icon name="moon" size={13}/>Sombre</button></div>
    </aside>
    <main className="app-main">{children}</main>
  </div>;
}
