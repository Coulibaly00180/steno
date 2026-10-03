// Mirror of hostAllowed in backend/app/auth.py (host_allowed): keep both in step.
const LOCAL_NAME_SUFFIXES = [".local", ".lan", ".home", ".home.arpa", ".internal", ".localhost"];

/**
 * Is this Host header a name Sténo is reached by? Refuses public domain names (DNS rebinding:
 * a page of attacker.example pointed at 127.0.0.1 would otherwise talk to Sténo as "this computer").
 */
export function hostAllowed(host: string | null | undefined): boolean {
  if (!host) return true;
  let name = host.trim().toLowerCase();
  if (name.startsWith("[")) return name.includes("]");
  const colon = name.lastIndexOf(":");
  if (colon >= 0) name = name.slice(0, colon);
  name = name.replace(/\.+$/, "");
  if (!name) return false;
  if (/^[0-9.]+$/.test(name)) return true;
  if (!name.includes(".")) return true;
  return LOCAL_NAME_SUFFIXES.some(suffix => name.endsWith(suffix));
}
