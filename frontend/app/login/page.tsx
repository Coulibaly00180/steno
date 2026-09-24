"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../lib/api";
import { accessStatus, safeNext, type AccessStatus } from "../../lib/access";
import { Icon } from "../../components/Icons";

/** n°15: the password asked on the network (or on this computer too, if chosen). */
export default function LoginPage() {
  const [status, setStatus] = useState<AccessStatus | null>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);

  useEffect(() => {
    accessStatus().then(result => {
      setStatus(result);
      // Nothing to ask (no password, or already signed in): straight back.
      if (!result.required || result.authenticated) window.location.replace(safeNext(new URLSearchParams(window.location.search).get("next")));
    }).catch(reason => setError(String(reason)));
  }, []);

  async function login(event: FormEvent) {
    event.preventDefault();
    setWorking(true); setError("");
    try {
      await api("/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }) });
      window.location.replace(safeNext(new URLSearchParams(window.location.search).get("next")));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason)); setPassword(""); setWorking(false);
    }
  }

  return <div className="login-page">
    <form className="card login-card" onSubmit={login}>
      <span className="brand-lockup"><span className="brand-mark"><i /></span><span className="brand-name">Sténo</span></span>
      {status?.network_blocked
        ? <p className="muted">L&apos;accès depuis le réseau est fermé : définissez d&apos;abord un mot de passe sur l&apos;ordinateur où Sténo est installé (Paramètres › Accès et sécurité).</p>
        : <>
          <h1><Icon name="lock" size={18} /> Connexion</h1>
          <label htmlFor="password">Mot de passe</label>
          <input id="password" type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" autoFocus required />
          <button className="btn primary" disabled={working || !password}>{working ? "Vérification…" : "Se connecter"}</button>
          <p className="field-hint">La session reste ouverte 30 jours sur cet appareil.</p>
        </>}
      {error && <div className="error" role="alert">{error}</div>}
    </form>
  </div>;
}
