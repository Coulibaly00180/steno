"use client";

import { useEffect, useRef, useState } from "react";
import { safeStorage } from "./analysis";
import { TERMINAL_STATUSES as TERMINAL } from "./jobs";

const PREFERENCE_KEY = "video-ai-notify";

type JobLike = { id: string; status: string; progress: number } | null;

function notificationsSupported() {
  return typeof window !== "undefined" && "Notification" in window && window.isSecureContext;
}

/**
 * Tab title shows progress, and a system notification is sent when the job ends
 * while the tab is in the background (F-15.1 to F-15.5).
 */
export function useJobNotifications(job: JobLike, filename: string | undefined) {
  const [supported, setSupported] = useState(false);
  const [permission, setPermission] = useState<NotificationPermission>("default");
  const [wanted, setWanted] = useState(false);
  const baseTitle = useRef<string | null>(null);
  const previousStatus = useRef<string | null>(null);

  useEffect(() => {
    const available = notificationsSupported();
    setSupported(available);
    if (!available) return;
    setPermission(Notification.permission);
    setWanted(Notification.permission === "granted" && safeStorage()?.getItem(PREFERENCE_KEY) === "1");
  }, []);

  useEffect(() => {
    baseTitle.current = document.title;
    return () => { if (baseTitle.current !== null) document.title = baseTitle.current; };
  }, []);

  useEffect(() => {
    if (!job || !filename) return;
    const before = previousStatus.current;
    previousStatus.current = job.status;
    const finished = TERMINAL.includes(job.status);
    if (!finished) { document.title = `(${job.progress} %) ${filename}`; return; }
    // Only a transition observed on this page counts as "just finished".
    if (!before || TERMINAL.includes(before)) return;
    const title = job.status === "COMPLETED" ? `✓ Terminé · ${filename}` : job.status === "CANCELLED" ? `Annulé · ${filename}` : `✕ Échec · ${filename}`;
    document.title = title;
    // The user cancelled it themselves: no notification.
    if (job.status === "CANCELLED") return;
    if (wanted && supported && Notification.permission === "granted" && document.visibilityState !== "visible") {
      const notification = new Notification(title, {
        body: job.status === "COMPLETED" ? "L'analyse est prête." : "Le traitement a échoué.",
        tag: job.id, // one notification per job, even with two tabs open
      });
      notification.onclick = () => { window.focus(); notification.close(); };
    }
  }, [job?.id, job?.status, job?.progress, filename, wanted, supported]);

  async function setNotify(enabled: boolean) {
    if (!supported) return;
    let current = Notification.permission;
    if (enabled && current === "default") current = await Notification.requestPermission();
    setPermission(current);
    const active = enabled && current === "granted";
    setWanted(active);
    try { safeStorage()?.setItem(PREFERENCE_KEY, active ? "1" : "0"); } catch { /* convenience only */ }
  }

  return { supported, permission, wanted, setNotify };
}
