import { api } from "./api";

/** n°15: who is asking, and whether a password is needed for it. */
export type AccessStatus = {
  password_set: boolean; require_local: boolean; remote: boolean;
  required: boolean; authenticated: boolean; network_blocked: boolean;
};

export type NetworkInfo = { https_ready: boolean; address: string | null; port: number; url: string | null };

export const accessStatus = () => api<AccessStatus>("/auth/status");

/** A path inside Sténo to come back to after the login (never another site). */
export function safeNext(value: string | null) {
  return value && value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/login") ? value : "/";
}
