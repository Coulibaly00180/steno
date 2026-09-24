"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { API, api } from "../lib/api";
import { accessStatus, type AccessStatus, type NetworkInfo } from "../lib/access";
import { Icon } from "./Icons";

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** n°15: the optional password, and how to open Sténo to the other devices of the local network. */
export default function AccessPanel() {
  const [status, setStatus] = useState<AccessStatus | null>(null);
  const [network, setNetwork] = useState<NetworkInfo | null>(null);
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try { const [access, info] = await Promise.all([accessStatus(), api<NetworkInfo>("/network")]); setStatus(access); setNetwork(info); }
    catch (reason) { setError(String(reason)); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  // The current password is asked from the network, or when it is asked on this computer too.
  const needsCurrent = !!status?.password_set && (status.remote || status.require_local);

  async function send(body: Record<string, unknown>, done: string) {
    setError(""); setMessage("");
    try {
      setStatus(await api<AccessStatus>("/auth/password", json("PUT", needsCurrent ? { ...body, current_password: current } : body)));
      setMessage(done); setEditing(false); setCurrent(""); setPassword(""); setConfirm("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirm) { setError("Les deux mots de passe diffèrent"); return; }
    void send({ new_password: password }, status?.password_set ? "Mot de passe changé : les autres appareils doivent se reconnecter." : "Mot de passe défini.");
  }

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    window.location.assign("/login");
  }

  if (!status) return error ? <section className="card settings-section" id="acces"><h2>Accès et sécurité</h2><div className="error">{error}</div></section> : null;
  const form = <form className="password-form" onSubmit={submit}>
    {needsCurrent && <label>Mot de passe actuel<input type="password" value={current} onChange={event => setCurrent(event.target.value)} autoComplete="current-password" required /></label>}
    <label>{status.password_set ? "Nouveau mot de passe" : "Mot de passe"}<input type="password" value={password} onChange={event => setPassword(event.target.value)} minLength={8} autoComplete="new-password" required /></label>
    <label>Confirmation<input type="password" value={confirm} onChange={event => setConfirm(event.target.value)} minLength={8} autoComplete="new-password" required /></label>
    <div className="row"><button className="btn small primary">Enregistrer</button>{status.password_set && <button type="button" className="btn small" onClick={() => setEditing(false)}>Annuler</button>}</div>
    <p className="field-hint">8 caractères au moins. Une phrase de quelques mots est plus sûre et plus facile à retenir.</p>
  </form>;

  return <section className="card settings-section" id="acces">
    <div className="spread"><h2><Icon name="lock" size={15} /> Accès et sécurité</h2>{status.required && status.authenticated && <button type="button" className="btn small" onClick={() => void logout()}>Se déconnecter</button>}</div>
    <p className="muted">Sténo n&apos;écoute que sur cet ordinateur. Pour l&apos;ouvrir depuis un téléphone ou un autre ordinateur du réseau local, définissez un mot de passe : sans lui, les requêtes venant du réseau sont refusées.</p>
    {status.password_set ? <>
      <div className="setting-row"><span>Mot de passe</span><span className="service-ok"><i />défini</span></div>
      <label className="checkbox"><input type="checkbox" checked={status.require_local} onChange={event => void send({ require_local: event.target.checked }, event.target.checked ? "Le mot de passe est aussi demandé sur cet ordinateur." : "Plus de mot de passe sur cet ordinateur.")} disabled={needsCurrent && !current} /><span>Le demander aussi sur cet ordinateur</span></label>
      {needsCurrent && !editing && <label className="field-hint">Mot de passe actuel (pour modifier ces réglages) <input type="password" value={current} onChange={event => setCurrent(event.target.value)} autoComplete="current-password" /></label>}
      {editing ? form : <div className="row">
        <button type="button" className="btn small" onClick={() => setEditing(true)}>Changer le mot de passe</button>
        <button type="button" className="btn small danger" onClick={() => { if (window.confirm("Supprimer le mot de passe ? L'accès depuis le réseau sera de nouveau fermé.")) void send({ new_password: null }, "Mot de passe supprimé : l'accès depuis le réseau est fermé."); }} disabled={needsCurrent && !current}>Supprimer le mot de passe</button>
      </div>}
    </> : form}
    {message && <p className="success-note" role="status">{message}</p>}
    {error && <div className="error" role="alert">{error}</div>}

    <h3 className="field-label">Accès depuis le réseau local (HTTPS)</h3>
    <ol className="network-steps">
      <li className={status.password_set ? "done" : undefined}>Définissez un mot de passe (ci-dessus).</li>
      <li className={network?.https_ready ? "done" : undefined}>Démarrez le proxy HTTPS : <code>docker compose --profile reseau up -d</code>{network?.https_ready ? " — il a déjà démarré une fois." : ""}</li>
      <li>Depuis l&apos;autre appareil, ouvrez {network?.url ? <strong className="mono">{network.url}</strong> : <span>https://&lt;adresse de cet ordinateur&gt;:{network?.port ?? 8443}</span>}{network?.url ? "" : " (l'adresse se règle dans .env : LAN_ADDRESS)"}.</li>
      <li>Le navigateur avertit d&apos;un certificat inconnu : il vient de l&apos;autorité locale de Sténo. {network?.https_ready ? <><a className="text-link" href={`${API}/network/certificate`}>Téléchargez-la</a> et installez-la une fois sur chaque appareil pour ne plus voir l&apos;avertissement.</> : "Elle sera téléchargeable ici après le premier démarrage du proxy."}</li>
    </ol>
    <p className="field-hint">Le pare-feu de Windows peut demander d&apos;autoriser Docker sur le réseau privé : acceptez. N&apos;ouvrez pas ce port vers Internet.</p>
  </section>;
}
