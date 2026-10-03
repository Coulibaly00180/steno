"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { API, api, responseError } from "../lib/api";
import type { AccessToken } from "../lib/access";

const dateTime = (value: string) => new Date(value).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });

/** Feuille de route n° 4, phase 1: tokens for the browser extension and scripts, shown once, revoked at once. */
export default function AccessTokens() {
  const [tokens, setTokens] = useState<AccessToken[] | null>(null);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<"import" | "full">("import");
  const [created, setCreated] = useState<AccessToken | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { setTokens(await api<AccessToken[]>("/access/tokens")); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError(""); setCopied(false);
    try {
      setCreated(await api<AccessToken>("/access/tokens", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name.trim(), scope }) }));
      setName(""); setScope("import"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  async function revoke(token: AccessToken) {
    if (!window.confirm(`Révoquer le jeton « ${token.name} » ? Ce qui l'utilise sera refusé dès sa prochaine requête.`)) return;
    setError("");
    try {
      const res = await fetch(`${API}/access/tokens/${token.id}`, { method: "DELETE" });
      if (!res.ok) throw await responseError(res);
      if (created?.id === token.id) setCreated(null);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  async function copy() {
    if (!created?.token) return;
    try { await navigator.clipboard.writeText(created.token); setCopied(true); }
    catch { setError("Copie impossible : sélectionnez le jeton et copiez-le à la main."); }
  }

  return <div className="access-tokens" id="jetons">
    <h3 className="field-label">Jetons d&apos;accès</h3>
    <p className="field-hint">Pour l&apos;extension « Envoyer à Sténo » et les scripts : un jeton remplace le mot de passe, sans ouvrir de session. Il ne permet ni de gérer les jetons, ni de changer le mot de passe ; changer ou supprimer le mot de passe les révoque tous. Créez-en un par appareil ou par usage, et révoquez celui qui ne sert plus.</p>
    <form className="row token-form" onSubmit={create}>
      <input type="text" value={name} onChange={event => setName(event.target.value)} placeholder="Nom (ex. : Extension Firefox du portable)" maxLength={80} required aria-label="Nom du jeton" />
      <select value={scope} onChange={event => setScope(event.target.value as "import" | "full")} aria-label="Portée du jeton"><option value="import">Envoyer des liens (extension)</option><option value="full">Accès complet (scripts)</option></select>
      <button className="btn small">Créer un jeton</button>
    </form>
    {created?.token && <div className="token-created" role="status">
      <p><strong>Jeton « {created.name} »</strong> : copiez-le maintenant, il ne sera plus affiché.</p>
      <div className="row"><code className="mono token-value">{created.token}</code><button type="button" className="btn small" onClick={() => void copy()}>{copied ? "Copié" : "Copier"}</button></div>
    </div>}
    {error && <div className="error" role="alert">{error}</div>}
    {tokens && tokens.length > 0 && <ul className="token-list">{tokens.map(token => <li key={token.id}>
      <span><strong>{token.name}</strong> <span className="mono muted">{token.prefix}…</span> <span className="pill">{token.scope === "full" ? "accès complet" : "envoyer des liens"}</span><span className="field-hint">Créé le {dateTime(token.created_at)} · {token.last_used_at ? `utilisé le ${dateTime(token.last_used_at)}` : "jamais utilisé"}</span></span>
      <button type="button" className="btn small danger" onClick={() => void revoke(token)}>Révoquer</button>
    </li>)}</ul>}
    {tokens && tokens.length === 0 && <p className="field-hint">Aucun jeton.</p>}
  </div>;
}
