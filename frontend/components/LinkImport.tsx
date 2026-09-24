"use client";

import { useState } from "react";
import { api, formatBytes, formatDuration } from "../lib/api";

export type LinkItem = { url: string; title: string; detail: string; selected: boolean };
type Preview =
  | { kind: "media"; url: string; title: string; filename: string; size_bytes: number | null; content_type: string | null }
  | { kind: "feed"; title: string; episodes: { title: string; url: string; published: string | null; duration_seconds: number | null; size_bytes: number | null }[] };

const MAX_EPISODES_SHOWN = 30;

/** n°12: a direct link to a file, or a podcast feed whose episodes are listed. */
export default function LinkImport({ items, onItems, confirmed, onConfirmed, disabled }: {
  items: LinkItem[]; onItems: (items: LinkItem[]) => void; confirmed: boolean; onConfirmed: (value: boolean) => void; disabled: boolean;
}) {
  const [url, setUrl] = useState("");
  const [feedTitle, setFeedTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function inspect() {
    if (!url.trim() || busy) return;
    setBusy(true); setError(""); onItems([]); setFeedTitle("");
    try {
      const preview = await api<Preview>("/imports/url/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: url.trim() }) });
      if (preview.kind === "media") {
        onItems([{ url: preview.url, title: preview.title, detail: [preview.filename, preview.size_bytes ? formatBytes(preview.size_bytes) : ""].filter(Boolean).join(" · "), selected: true }]);
      } else {
        setFeedTitle(preview.title);
        onItems(preview.episodes.slice(0, MAX_EPISODES_SHOWN).map((episode, index) => ({
          url: episode.url, title: episode.title, selected: index === 0,
          detail: [episode.published ? new Date(episode.published).toLocaleDateString("fr-FR") : "", episode.duration_seconds ? formatDuration(episode.duration_seconds).replace(/^00:/, "") : "", episode.size_bytes ? formatBytes(episode.size_bytes) : ""].filter(Boolean).join(" · "),
        })));
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  const toggle = (index: number) => onItems(items.map((item, position) => position === index ? { ...item, selected: !item.selected } : item));

  return <div className="card link-import">
    {/* Not a <form>: this sits inside the import form, and nested forms are invalid HTML. */}
    <div className="link-form">
      <label htmlFor="link-url" className="field-label">Lien vers un fichier audio ou vidéo, ou flux RSS d&apos;un podcast</label>
      <div className="row"><input id="link-url" type="url" value={url} onChange={event => setUrl(event.target.value)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); void inspect(); } }} placeholder="https://exemple.org/episode.mp3 ou https://exemple.org/podcast.rss" maxLength={2000} disabled={disabled} /><button type="button" className="btn" onClick={() => void inspect()} disabled={disabled || busy || !url.trim()}>{busy ? "Vérification…" : "Vérifier le lien"}</button></div>
      <span className="field-hint">Les pages des plateformes vidéo (YouTube…) ne sont pas prises en charge : seuls les fichiers mis à disposition au téléchargement le sont.</span>
    </div>
    {error && <div className="error" role="alert">{error}</div>}
    {!!items.length && <>
      {feedTitle && <p className="field-label">{feedTitle} · choisissez les épisodes</p>}
      <ul className="link-items">{items.map((item, index) => <li key={item.url}><label className="checkbox"><input type="checkbox" checked={item.selected} onChange={() => toggle(index)} disabled={disabled} /><span><strong>{item.title}</strong>{item.detail && <span className="field-hint">{item.detail}</span>}</span></label></li>)}</ul>
      <label className="checkbox rights-check"><input type="checkbox" checked={confirmed} onChange={event => onConfirmed(event.target.checked)} disabled={disabled} /><span>J&apos;ai le droit d&apos;utiliser ce contenu (contenu libre, publié pour être téléchargé, ou dont je suis l&apos;auteur). Le lien est conservé avec l&apos;analyse.</span></label>
    </>}
  </div>;
}
