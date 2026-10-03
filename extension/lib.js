// Shared logic of the "Envoyer à Sténo" extension (feuille de route n° 4, phase 1).
//
// Every request of the extension goes through `stenoFetch`: it only ever
// reaches the Sténo address configured in the options, under /api/. Pure
// functions, tested with `node --test` (extension/tests/lib.test.js).
(function (root) {
  "use strict";

  const DEFAULT_ORIGIN = "http://127.0.0.1:3000";
  const API_PREFIX = "/api/";
  const MAX_TITLE = 200;

  /** "https://192.168.1.20:8443/videos" → "https://192.168.1.20:8443"; throws on anything else than http(s). */
  function normalizeOrigin(input) {
    const text = String(input || "").trim();
    let parsed;
    try {
      parsed = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `http://${text}`);
    } catch {
      throw new Error("Adresse invalide : par exemple http://127.0.0.1:3000 ou https://192.168.1.20:8443");
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") throw new Error("L'adresse doit commencer par http:// ou https://");
    if (parsed.username || parsed.password) throw new Error("L'adresse ne doit pas contenir d'identifiant ni de mot de passe");
    if (!parsed.hostname) throw new Error("Adresse invalide");
    return parsed.origin;
  }

  /** The host permission asked for this address (match patterns take no port: the code checks it). */
  function permissionPattern(origin) {
    const parsed = new URL(normalizeOrigin(origin));
    return `${parsed.protocol}//${parsed.hostname}/*`;
  }

  /** The full URL of an API path on the configured Sténo, and nowhere else. */
  function apiUrl(origin, path) {
    const base = normalizeOrigin(origin);
    if (typeof path !== "string" || !path.startsWith(API_PREFIX) || path.includes("..") || path.includes("//") || path.includes("\\") || /[\s@]/.test(path)) {
      throw new Error(`Chemin refusé : ${path}`);
    }
    const url = new URL(base + path);
    if (url.origin !== base) throw new Error("Adresse refusée : seule l'adresse de Sténo configurée est contactée");
    return url.href;
  }

  /** Pages that may be sent: http(s) only, never Sténo itself. */
  function isSendable(url, origin) {
    let parsed;
    try { parsed = new URL(url); } catch { return false; }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
    if (parsed.username || parsed.password) return false;
    try { if (origin && parsed.origin === normalizeOrigin(origin)) return false; } catch { /* no address yet */ }
    return true;
  }

  /** "(3) Une conférence - YouTube" → "Une conférence". */
  function cleanTitle(title) {
    let text = String(title || "").replace(/\s+/g, " ").trim();
    text = text.replace(/^\(\d+\+?\)\s*/, "");
    text = text.replace(/\s+[-–—|]\s+(YouTube|Vimeo|Dailymotion|PeerTube|Twitch|SoundCloud)$/i, "");
    return text.slice(0, MAX_TITLE).trim();
  }

  /** The body of POST /imports/url; options left empty keep Sténo's defaults. */
  function importBody(url, title, options) {
    const body = { url };
    const name = cleanTitle(title);
    if (name) body.title = name;
    const chosen = options || {};
    if (chosen.templateId) body.template_id = String(chosen.templateId);
    if (chosen.diarize) {
      body.diarize = true;
      const count = Number(chosen.numSpeakers);
      if (Number.isInteger(count) && count >= 1 && count <= 20) body.num_speakers = count;
    }
    return body;
  }

  function requestInit(config, init) {
    const headers = Object.assign({}, (init && init.headers) || {});
    if (config.token) headers.Authorization = `Bearer ${config.token}`;
    // No cookie: the token is the only credential, and a redirect elsewhere is an error.
    return Object.assign({}, init || {}, { headers, credentials: "omit", redirect: "error", cache: "no-store" });
  }

  /** The message shown for a failed request. */
  function errorMessage(status, body) {
    let detail = "";
    try { const parsed = JSON.parse(body); if (typeof parsed.detail === "string") detail = parsed.detail; } catch { /* not JSON */ }
    if (status === 401) return detail === "Jeton d'accès invalide ou révoqué"
      ? "Jeton d'accès refusé (révoqué ?) : créez-en un autre dans Sténo, Paramètres › Accès et sécurité."
      : "Sténo demande un jeton d'accès : collez-le dans les options de l'extension.";
    if (status === 403 && detail) return detail;
    return detail || `Sténo a répondu par une erreur ${status}`;
  }

  /** A token travels over HTTPS, or over plain HTTP to this computer only. */
  function tokenTransportOk(origin) {
    const parsed = new URL(normalizeOrigin(origin));
    return parsed.protocol === "https:" || ["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname);
  }

  /** fetch() on the configured Sténo only; resolves to the parsed JSON, rejects with a message for the user. */
  async function stenoFetch(config, path, init, fetchImpl) {
    const url = apiUrl(config.origin, path);
    if (config.token && !tokenTransportOk(config.origin)) {
      throw new Error("Le jeton ne part pas en clair sur le réseau : utilisez l'adresse HTTPS de Sténo (Paramètres › Accès et sécurité).");
    }
    const doFetch = fetchImpl || root.fetch.bind(root);
    let response;
    try {
      response = await doFetch(url, requestInit(config, init));
    } catch {
      throw new Error(`Sténo ne répond pas à ${normalizeOrigin(config.origin)}. Est-il démarré ? En HTTPS, l'autorité locale est-elle installée ?`);
    }
    const text = await response.text();
    if (!response.ok) throw new Error(errorMessage(response.status, text));
    return text ? JSON.parse(text) : null;
  }

  const api = { DEFAULT_ORIGIN, normalizeOrigin, permissionPattern, apiUrl, isSendable, cleanTitle, importBody, requestInit, errorMessage, tokenTransportOk, stenoFetch };
  root.StenoLib = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : self);
