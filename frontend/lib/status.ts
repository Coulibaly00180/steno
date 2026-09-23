"use client";

import { useEffect, useState } from "react";
import { API } from "./api";

export type ServiceState = "ok" | "degraded" | "down";
export type ServiceName = "database" | "redis" | "worker" | "scheduler" | "ollama" | "model" | "embedding";
export type ServiceStatus = { status: ServiceState; detail: string | null };
export type SystemStatus = { overall: ServiceState; checked_at: string; services: Record<ServiceName, ServiceStatus> };

export const serviceLabels: Record<ServiceName, string> = {
  database: "Base de données",
  redis: "Redis",
  worker: "Worker",
  scheduler: "Dossier surveillé et sauvegardes",
  ollama: "Ollama",
  model: "Modèle LLM",
  embedding: "Recherche (embeddings)",
};

const POLL_INTERVAL_MS = 30_000;

/** Polls GET /status. `status` is null while loading; `reachable` is false when the API does not answer. */
export function useSystemStatus() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [reachable, setReachable] = useState<boolean | null>(null);

  useEffect(() => {
    let active = true;
    const check = async () => {
      try {
        const response = await fetch(`${API}/status`, { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const body: SystemStatus = await response.json();
        if (active) { setStatus(body); setReachable(true); }
      } catch {
        if (active) { setStatus(null); setReachable(false); }
      }
    };
    void check();
    const timer = window.setInterval(check, POLL_INTERVAL_MS);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  return { status, reachable };
}
