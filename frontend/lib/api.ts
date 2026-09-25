export const API = "/api";

export function errorFromBody(body: string, status: number): Error {
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    if (typeof parsed.detail === "string") return new Error(parsed.detail);
  } catch {
    // A non-JSON response is still useful as the fallback message below.
  }
  return new Error(body || `HTTP ${status}`);
}

export async function responseError(res: Response): Promise<Error> {
  return errorFromBody(await res.text(), res.status);
}

/** "12,4 Mo", "1,3 Go": sizes as the import form shows them. */
export function formatBytes(bytes: number) {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1).replace(".", ",")} Go`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(bytes >= 100 * 1024 ** 2 ? 0 : 1).replace(".", ",")} Mo`;
  return `${Math.max(1, Math.round(bytes / 1024))} Ko`;
}

/**
 * POST a form with upload progress: fetch cannot report bytes sent, XMLHttpRequest can (n°18).
 * Rejects with an AbortError when `signal` aborts.
 */
export function uploadForm<T>(path: string, form: FormData, onProgress: (loaded: number, total: number) => void, signal?: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `${API}${path}`);
    request.responseType = "text";
    request.upload.onprogress = event => { if (event.lengthComputable) onProgress(event.loaded, event.total); };
    request.onload = () => {
      if (request.status < 200 || request.status >= 300) { reject(errorFromBody(request.responseText, request.status)); return; }
      try { resolve(JSON.parse(request.responseText) as T); } catch { reject(new Error("Réponse invalide du serveur")); }
    };
    request.onerror = () => reject(new Error("Envoi interrompu : la connexion a été perdue"));
    request.ontimeout = () => reject(new Error("Envoi interrompu : délai dépassé"));
    request.onabort = () => reject(new DOMException("Envoi annulé", "AbortError"));
    signal?.addEventListener("abort", () => request.abort(), { once: true });
    request.send(form);
  });
}

/** n°15: a session that expired (or a password set meanwhile) sends to the login page, then back here. */
export function goToLogin() {
  if (typeof window === "undefined" || window.location.pathname === "/login") return;
  const next = `${window.location.pathname}${window.location.search}`;
  window.location.assign(`/login?next=${encodeURIComponent(next)}`);
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, { ...init, cache: "no-store" });
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/")) goToLogin();
    throw await responseError(res);
  }
  return res.json();
}

/** Like api(), with the response headers (the library's total is in X-Total-Count). */
export async function apiWithHeaders<T>(path: string, init?: RequestInit): Promise<{ data: T; headers: Headers }> {
  const res = await fetch(`${API}${path}`, { ...init, cache: "no-store" });
  if (!res.ok) {
    if (res.status === 401) goToLogin();
    throw await responseError(res);
  }
  return { data: await res.json() as T, headers: res.headers };
}

export function formatDuration(seconds: number) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return [h, m, s].map((v) => String(v).padStart(2, "0")).join(":");
}
