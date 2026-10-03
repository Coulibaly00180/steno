import { NextResponse, type NextRequest } from "next/server";
import { hostAllowed } from "./lib/hosts";

/** Feuille de route n° 4, phase 1 (security review): no page nor API call under a public domain name (DNS rebinding). */
export function middleware(request: NextRequest) {
  if (!hostAllowed(request.headers.get("host"))) {
    return new NextResponse("Nom d'hôte refusé : ouvrez Sténo par son adresse (127.0.0.1, localhost ou l'adresse du réseau local).", {
      status: 421, headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }
  return NextResponse.next();
}

// Not /api: a middleware buffers request bodies, and uploads reach 2 GB. The API checks the
// Host and X-Forwarded-Host headers itself (AccessGuard, auth.host_allowed).
export const config = { matcher: ["/((?!api/|_next/static|_next/image).*)"] };
