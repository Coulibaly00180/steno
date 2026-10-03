import { api } from "./api";

/** n°15: who is asking, and whether a password is needed for it. */
export type AccessStatus = {
  password_set: boolean; require_local: boolean; remote: boolean;
  required: boolean; authenticated: boolean; network_blocked: boolean;
};

export type NetworkInfo = { https_ready: boolean; address: string | null; port: number; url: string | null };

export const accessStatus = () => api<AccessStatus>("/auth/status");

/** Feuille de route n° 4, phase 1: tokens of the browser extension and scripts; `token` only in the creation's answer. */
export type AccessToken = { id: number; name: string; prefix: string; scope: "import" | "full"; created_at: string; last_used_at: string | null; token?: string };

/** The first http(s) link of a shared text ("Regardez ça https://youtu.be/x"), or null. */
export function sharedLink(...values: (string | null | undefined)[]): string | null {
  for (const value of values) {
    for (const candidate of (value || "").match(/https?:\/\/[^\s<>"]+/gi) || []) {
      try {
        const url = new URL(candidate.replace(/[),.;!?»]+$/, ""));
        if ((url.protocol === "http:" || url.protocol === "https:") && !url.username && !url.password) return url.href;
      } catch {
        // Not a link: try the next one.
      }
    }
  }
  return null;
}

/** The bookmarklet: opens Sténo's /envoyer page with the address and title of the page being read. */
export function bookmarklet(origin: string) {
  const target = JSON.stringify(`${origin}/envoyer`);
  return `javascript:(()=>{window.open(${target}+"?url="+encodeURIComponent(location.href)+"&title="+encodeURIComponent(document.title),"_blank","noopener,noreferrer")})()`;
}

/** A path inside Sténo to come back to after the login (never another site). */
export function safeNext(value: string | null) {
  // Browsers read "/\evil.example" as "//evil.example": resolve the path and keep it only on this origin.
  if (!value || !/^\/[^/\\]/.test(value)) return "/";
  try {
    const target = new URL(value, window.location.origin);
    if (target.origin !== window.location.origin || target.pathname.startsWith("/login")) return "/";
    return `${target.pathname}${target.search}${target.hash}`;
  } catch {
    return "/";
  }
}
