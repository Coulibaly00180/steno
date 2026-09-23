"use client";

import { useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { Icon } from "./Icons";

export type Speaker = { id: number; position: number; name: string | null; label: string; seconds: number; share: number };

// Same colour for a speaker in this panel and in the transcript.
export const speakerColor = (position: number) => `var(--speaker-${((position - 1) % 8) + 1})`;

function speakingTime(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}`;
}

/**
 * Speakers of a video (n°8): rename « Intervenant 2 », merge a voice found
 * twice, or run the identification on a video imported without it.
 */
export default function SpeakersPanel({ videoId, speakers, canEdit, diarizationError, onChanged, onJobStarted }: {
  videoId: string;
  speakers: Speaker[];
  canEdit: boolean;
  diarizationError?: string | null;
  onChanged: (message: string) => void | Promise<void>;
  onJobStarted: (jobId: string) => void;
}) {
  const [renaming, setRenaming] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [count, setCount] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run(action: () => Promise<unknown>, message: string) {
    setBusy(true); setError("");
    try { await action(); await onChanged(message); return true; }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); return false; }
    finally { setBusy(false); }
  }

  async function rename(event: FormEvent, speaker: Speaker) {
    event.preventDefault();
    const saved = await run(() => api(`/videos/${videoId}/speakers/${speaker.id}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: draft.trim() || null }),
    }), "Intervenant renommé");
    if (saved) setRenaming(null);
  }

  function merge(speaker: Speaker, into: string) {
    if (!into) return;
    void run(() => api(`/videos/${videoId}/speakers/${speaker.id}/merge`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ into: Number(into) }),
    }), "Intervenants fusionnés");
  }

  async function detect(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const job = await api<{ id: string }>(`/videos/${videoId}/speakers/detect`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ num_speakers: count ? Number(count) : null }),
      });
      onJobStarted(job.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  const countField = <label className="speaker-count">Nombre d&apos;intervenants <input type="number" min={1} max={20} value={count} onChange={event => setCount(event.target.value)} placeholder="auto" disabled={busy} /></label>;

  if (!speakers.length) {
    if (!canEdit) return null;
    return <section className="card speakers-card">
      <div className="spread"><div><h2>Intervenants</h2><p className="field-hint">{diarizationError ? `L'identification a échoué : ${diarizationError}` : "Qui parle, et quand : utile pour une réunion ou une interview. Le résumé pourra ensuite attribuer décisions et actions."}</p></div>
        <form className="row" onSubmit={detect}>{countField}<button className="btn" disabled={busy}><Icon name="sparkle" size={14}/>{busy ? "Lancement…" : diarizationError ? "Réessayer" : "Identifier les intervenants"}</button></form></div>
      {error && <div className="error" role="alert">{error}</div>}
    </section>;
  }

  return <section className="card speakers-card">
    <div className="spread"><h2>Intervenants <span className="muted">({speakers.length})</span></h2>
      {canEdit && <details className="speaker-redo"><summary>Relancer l&apos;identification</summary><form className="row" onSubmit={detect}>{countField}<button className="btn" disabled={busy}>Relancer</button></form><p className="field-hint">Les noms donnés seront perdus.</p></details>}</div>
    <ul className="speaker-list">{speakers.map(speaker => <li key={speaker.id}>
      <span className="speaker-dot" style={{ background: speakerColor(speaker.position) }} aria-hidden="true" />
      {renaming === speaker.id
        ? <form className="speaker-rename" onSubmit={event => void rename(event, speaker)}><input value={draft} onChange={event => setDraft(event.target.value)} maxLength={80} autoFocus placeholder={`Intervenant ${speaker.position}`} aria-label={`Nom de ${speaker.label}`} onKeyDown={event => { if (event.key === "Escape") setRenaming(null); }} /><button className="btn small primary" disabled={busy}>OK</button></form>
        : <strong className="speaker-name">{speaker.label}</strong>}
      <span className="speaker-share" title={`${Math.round(speaker.share * 100)} % du temps de parole`}><i style={{ width: `${Math.round(speaker.share * 100)}%`, background: speakerColor(speaker.position) }} /></span>
      <span className="mono muted speaker-time">{speakingTime(speaker.seconds)} · {Math.round(speaker.share * 100)} %</span>
      {canEdit && renaming !== speaker.id && <span className="row speaker-actions">
        <button type="button" className="link-button" onClick={() => { setRenaming(speaker.id); setDraft(speaker.name || ""); }} disabled={busy}>Renommer</button>
        {speakers.length > 1 && <select value="" onChange={event => merge(speaker, event.target.value)} disabled={busy} aria-label={`Fusionner ${speaker.label} avec…`}><option value="">Fusionner avec…</option>{speakers.filter(other => other.id !== speaker.id).map(other => <option key={other.id} value={other.id}>{other.label}</option>)}</select>}
      </span>}
    </li>)}</ul>
    {error && <div className="error" role="alert">{error}</div>}
  </section>;
}
