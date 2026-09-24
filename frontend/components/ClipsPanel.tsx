"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { API, api, formatBytes, formatDuration } from "../lib/api";
import { Icon } from "./Icons";

type Clip = {
  id: string; title: string; start_seconds: number; end_seconds: number; subtitles: "none" | "track" | "burned";
  subtitle_source: "original" | "translation"; status: "QUEUED" | "RUNNING" | "READY" | "FAILED" | "CANCELLED";
  error: string | null; progress: number | null; size_bytes: number | null; filename: string | null;
};
export type ClipRange = { start: number; end: number; title: string; nonce: number };

const subtitleLabels = { none: "Sans sous-titres", track: "Sous-titres activables", burned: "Sous-titres incrustés" };

/** "1:02:03", "02:03" or "123" (seconds) → seconds; null when unreadable. */
export function parseClock(value: string): number | null {
  const text = value.trim().replace(",", ".");
  if (!text) return null;
  const parts = text.split(":");
  if (parts.length > 3 || parts.some(part => !/^\d+(\.\d+)?$/.test(part))) return null;
  return parts.reduce((total, part) => total * 60 + Number(part), 0);
}

/** n°7: cut a chapter or a range out of the video, to share the right passage. */
export default function ClipsPanel({ videoId, duration, isAudio, hasTranslation, range, currentTime }: {
  videoId: string; duration: number; isAudio: boolean; hasTranslation: boolean; range: ClipRange | null; currentTime: () => number | null;
}) {
  const [clips, setClips] = useState<Clip[]>([]);
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [title, setTitle] = useState("");
  const [subtitles, setSubtitles] = useState<Clip["subtitles"]>("burned");
  const [source, setSource] = useState<Clip["subtitle_source"]>("original");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const section = useRef<HTMLElement>(null);

  const load = useCallback(async () => {
    try { setClips(await api<Clip[]>(`/videos/${videoId}/clips`)); }
    catch (reason) { setError(String(reason)); }
  }, [videoId]);
  useEffect(() => { void load(); }, [load]);
  const working = clips.some(clip => clip.status === "QUEUED" || clip.status === "RUNNING");
  useEffect(() => {
    if (!working) return;
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
  }, [working, load]);

  // A chapter's scissors: the form opens with its range.
  useEffect(() => {
    if (!range) return;
    setStart(formatDuration(range.start)); setEnd(formatDuration(range.end)); setTitle(range.title); setOpen(true); setError("");
    section.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [range]);

  function takePosition(set: (value: string) => void) {
    const seconds = currentTime();
    if (seconds != null) set(formatDuration(seconds));
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    const from = parseClock(start), to = parseClock(end);
    if (from == null || to == null) { setError("Début et fin au format hh:mm:ss"); return; }
    if (to <= from) { setError("La fin doit suivre le début"); return; }
    setCreating(true); setError("");
    try {
      await api(`/videos/${videoId}/clips`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_seconds: from, end_seconds: Math.min(to, duration), title: title.trim() || null, subtitles, subtitle_source: source }),
      });
      setOpen(false); setTitle(""); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setCreating(false); }
  }

  async function remove(clip: Clip) {
    if (!window.confirm(`Supprimer l'extrait « ${clip.title} » ?`)) return;
    try { await api(`/clips/${clip.id}`, { method: "DELETE" }); await load(); }
    catch (reason) { setError(String(reason)); }
  }

  const from = parseClock(start), to = parseClock(end);
  const length = from != null && to != null && to > from ? to - from : null;
  return <section className="card clips-panel" ref={section}>
    <div className="spread">
      <h2><Icon name="scissors" size={15} /> Extraits</h2>
      {!open && <button type="button" className="btn small" onClick={() => { setOpen(true); if (!start) takePosition(setStart); }}>+ Nouvel extrait</button>}
    </div>
    {!open && !clips.length && <p className="muted">Découpez un chapitre (ciseaux dans la liste des chapitres) ou un passage pour le partager{isAudio ? "" : ", avec les sous-titres si vous le souhaitez"}.</p>}
    {open && <form className="clip-form" onSubmit={create}>
      <label>Début<span className="row"><input value={start} onChange={event => setStart(event.target.value)} placeholder="00:12:30" aria-label="Début" required /><button type="button" className="btn small" onClick={() => takePosition(setStart)} title="Position actuelle de la lecture">Ici</button></span></label>
      <label>Fin<span className="row"><input value={end} onChange={event => setEnd(event.target.value)} placeholder="00:14:00" aria-label="Fin" required /><button type="button" className="btn small" onClick={() => takePosition(setEnd)} title="Position actuelle de la lecture">Ici</button></span></label>
      <label className="clip-title">Titre<input value={title} onChange={event => setTitle(event.target.value)} maxLength={200} placeholder="Par défaut : le chapitre, ou le passage" /></label>
      {!isAudio && <label>Sous-titres<select value={subtitles} onChange={event => setSubtitles(event.target.value as Clip["subtitles"])}>
        <option value="burned">Incrustés dans l&apos;image</option><option value="track">Piste activable</option><option value="none">Aucun</option>
      </select></label>}
      {!isAudio && subtitles !== "none" && hasTranslation && <label>Texte<select value={source} onChange={event => setSource(event.target.value as Clip["subtitle_source"])}><option value="original">Transcription</option><option value="translation">Traduction</option></select></label>}
      <p className="field-hint clip-hint">{length != null ? `Durée : ${formatDuration(length)}. ` : ""}{isAudio ? "Source sans image : l'extrait sera un fichier audio (.m4a)." : subtitles === "burned" ? "Incrustés : lisibles partout (messageries, réseaux), mais impossibles à retirer." : subtitles === "track" ? "Piste : le lecteur les affiche à la demande (VLC, lecteurs de bureau)." : "Vidéo seule."}</p>
      <div className="row"><button className="btn small primary" disabled={creating}>{creating ? "Création…" : "Créer l'extrait"}</button><button type="button" className="btn small" onClick={() => { setOpen(false); setError(""); }}>Annuler</button></div>
    </form>}
    {error && <div className="error" role="alert">{error}</div>}
    {!!clips.length && <ul className="clip-list">{clips.map(clip => <li key={clip.id}>
      <div className="clip-body">
        <strong>{clip.title}</strong>
        <span className="field-hint mono">{formatDuration(clip.start_seconds)} → {formatDuration(clip.end_seconds)} · {formatDuration(clip.end_seconds - clip.start_seconds)}{isAudio ? "" : ` · ${subtitleLabels[clip.subtitles]}${clip.subtitles !== "none" && clip.subtitle_source === "translation" ? " (traduction)" : ""}`}{clip.size_bytes ? ` · ${formatBytes(clip.size_bytes)}` : ""}</span>
        {(clip.status === "QUEUED" || clip.status === "RUNNING") && <div className="progress-track small" role="progressbar" aria-label="Découpe" aria-valuemin={0} aria-valuemax={100} aria-valuenow={clip.progress ?? 0}><div style={{ width: `${clip.progress ?? 0}%` }} /></div>}
        {clip.status === "QUEUED" && <span className="field-hint">En attente dans la file…</span>}
        {clip.status === "FAILED" && <span className="error inline-error">{clip.error || "La découpe a échoué"}</span>}
        {clip.status === "CANCELLED" && <span className="field-hint">Découpe annulée</span>}
      </div>
      <span className="row">
        {clip.status === "READY" && <><a className="btn small" href={`${API}/clips/${clip.id}/file`} target="_blank" rel="noopener noreferrer">Voir</a><a className="btn small primary" href={`${API}/clips/${clip.id}/file?download=1`}><Icon name="download" size={12} />Télécharger</a></>}
        <button type="button" className="icon-btn small-icon" title={clip.status === "QUEUED" || clip.status === "RUNNING" ? "Annuler et supprimer" : "Supprimer"} onClick={() => void remove(clip)}><Icon name="trash" size={12} /></button>
      </span>
    </li>)}</ul>}
  </section>;
}
