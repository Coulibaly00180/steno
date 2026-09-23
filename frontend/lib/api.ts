export const API = "/api";

export async function responseError(res: Response): Promise<Error> {
  const body = await res.text();
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    if (typeof parsed.detail === "string") return new Error(parsed.detail);
  } catch {
    // A non-JSON response is still useful as the fallback message below.
  }
  return new Error(body || `HTTP ${res.status}`);
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, { ...init, cache: "no-store" });
  if (!res.ok) {
    throw await responseError(res);
  }
  return res.json();
}

export function formatDuration(seconds: number) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return [h, m, s].map((v) => String(v).padStart(2, "0")).join(":");
}
