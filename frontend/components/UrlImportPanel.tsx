"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";

/** n°12: video platforms through yt-dlp, off by default; the rights stay the user's responsibility. */
export default function UrlImportPanel() {
  const [platforms, setPlatforms] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    api<{ platforms: boolean }>("/settings/url-import").then(value => setPlatforms(value.platforms)).catch(reason => setError(String(reason)));
  }, []);

  async function change(value: boolean) {
    setBusy(true); setError(""); setMessage("");
    try {
      const saved = await api<{ platforms: boolean }>("/settings/url-import", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ platforms: value }) });
      setPlatforms(saved.platforms);
      setMessage(saved.platforms ? "Plateformes vidéo activées pour l'import par lien." : "Plateformes vidéo désactivées.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  return <section className="card settings-section" id="import-liens">
    <h2>Import de liens</h2>
    <p className="muted">Les liens directs vers un fichier audio ou vidéo et les flux de podcasts sont toujours acceptés. Les pages des plateformes vidéo (YouTube, Vimeo, Dailymotion, PeerTube…) demandent cette option : seul l&apos;audio est téléchargé, avec l&apos;outil libre yt-dlp.</p>
    <label className="checkbox"><input type="checkbox" checked={!!platforms} disabled={platforms === null || busy} onChange={event => void change(event.target.checked)} /><span>Autoriser l&apos;import depuis les plateformes vidéo</span></label>
    <p className="field-hint platform-warning">Les conditions d&apos;utilisation de ces plateformes interdisent en général le téléchargement, et le droit d&apos;auteur s&apos;applique aux vidéos. N&apos;importez que des contenus que vous avez le droit d&apos;utiliser : les vôtres, ceux sous licence libre, ou ceux que leur auteur autorise. Les plateformes modifient souvent leur site : si les imports échouent, reconstruisez l&apos;application après avoir mis à jour yt-dlp.</p>
    {message && <p className="success-note" role="status">{message}</p>}
    {error && <div className="error" role="alert">{error}</div>}
  </section>;
}
