"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, formatDuration } from "../../../lib/api";
import { Icon } from "../../../components/Icons";
import type { EntityRow } from "../page";

type Mention = { start_seconds: number; context: string };
type VideoMentions = { video_id: string; title: string; created_at: string; duration_seconds: number; mentions: Mention[] };
type EntityPage = EntityRow & { merged_into: EntityRow | null; appearances: VideoMentions[] };

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export default function EntityDetail() {
  const { id } = useParams<{ id: string }>();
  const [entity, setEntity] = useState<EntityPage | null>(null);
  const [error, setError] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [mergeQuery, setMergeQuery] = useState("");
  const [candidates, setCandidates] = useState<EntityRow[]>([]);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try { setEntity(await api<EntityPage>(`/entities/${id}`)); setError(""); }
    catch (reason) { setError(String(reason)); }
  }, [id]);
  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (mergeQuery.trim().length < 2 || !entity) { setCandidates([]); return; }
    const timer = window.setTimeout(() => {
      api<EntityRow[]>(`/entities?q=${encodeURIComponent(mergeQuery.trim())}`)
        .then(rows => setCandidates(rows.filter(row => row.id !== entity.id).slice(0, 8)))
        .catch(() => setCandidates([]));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [mergeQuery, entity]);

  async function rename(name: string) {
    setRenaming(false);
    if (!entity || !name.trim() || name.trim() === entity.name) return;
    try { await api(`/entities/${entity.id}`, json("PATCH", { name: name.trim() })); await load(); }
    catch (reason) { setError(String(reason)); }
  }

  async function toggleHidden() {
    if (!entity) return;
    try { await api(`/entities/${entity.id}`, json("PATCH", { hidden: !entity.hidden })); await load(); setMessage(entity.hidden ? "Fiche de nouveau affichée." : "Fiche masquée : elle n'apparaît plus dans la liste ni sur les vidéos."); }
    catch (reason) { setError(String(reason)); }
  }

  async function mergeInto(target: EntityRow) {
    if (!entity || !window.confirm(`Fusionner « ${entity.name} » dans « ${target.name} » ? Ses mentions, présentes et futures, iront à « ${target.name} ».`)) return;
    try { await api(`/entities/${entity.id}/merge`, json("POST", { into: target.id })); window.location.href = `/entities/${target.id}`; }
    catch (reason) { setError(String(reason)); }
  }

  if (error && !entity) return <div className="page"><div className="error">{error}</div></div>;
  if (!entity) return <div className="page"><p className="muted">Chargement…</p></div>;
  return <div className="page wide">
    <p className="breadcrumb"><Link className="text-link" href="/entities">Personnes et dates</Link> › {entity.kind_label}</p>
    <header className="detail-header">
      <div>
        {renaming
          ? <input className="rename-input entity-rename" defaultValue={entity.name} maxLength={120} autoFocus aria-label="Nom affiché" onBlur={event => void rename(event.target.value)} onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); if (event.key === "Escape") setRenaming(false); }} />
          : <h1 className="detail-title">{entity.name}</h1>}
        <div className="meta"><span className={`entity-kind kind-${entity.kind}`}>{entity.kind_label}</span><span className="pill">{entity.videos} vidéo{entity.videos > 1 ? "s" : ""}</span><span className="pill">{entity.mentions} mention{entity.mentions > 1 ? "s" : ""}</span>{entity.hidden && <span className="pill">masquée</span>}</div>
        {entity.merged_into && <p className="field-hint">Fusionnée dans <Link className="text-link" href={`/entities/${entity.merged_into.id}`}>{entity.merged_into.name}</Link>.</p>}
      </div>
      <div className="row">
        {!!entity.appearances.length && <Link className="btn primary" href={`/ask?entity=${entity.id}`}><Icon name="chat" size={14}/>Poser une question</Link>}
        {!!entity.appearances.length && <Link className="btn" href={`/library?entity=${entity.id}&entity_name=${encodeURIComponent(entity.name)}`}><Icon name="library" size={14}/>Dans la bibliothèque</Link>}
        <button type="button" className="btn" onClick={() => setRenaming(true)}><Icon name="edit" size={14}/>Renommer</button>
        <button type="button" className="btn" onClick={() => void toggleHidden()}>{entity.hidden ? "Réafficher" : "Masquer"}</button>
      </div>
    </header>
    {message && <p className="success-note" role="status">{message}</p>}
    {error && <div className="error" role="alert">{error}</div>}

    <details className="card entity-merge">
      <summary>Même personne ou même chose sous un autre nom ? Fusionner</summary>
      <p className="field-hint">Cherchez la fiche qui doit rassembler les deux : les mentions de « {entity.name} » y seront réunies, y compris celles des prochaines vidéos.</p>
      <input type="search" value={mergeQuery} onChange={event => setMergeQuery(event.target.value)} placeholder="Nom de l'autre fiche…" aria-label="Fiche cible" maxLength={120} />
      {!!candidates.length && <ul className="merge-candidates">{candidates.map(row => <li key={row.id}><span><strong>{row.name}</strong> <span className="field-hint">{row.kind_label} · {row.videos} vidéo{row.videos > 1 ? "s" : ""}</span></span><button type="button" className="btn small" onClick={() => void mergeInto(row)}>Fusionner ici</button></li>)}</ul>}
    </details>

    <section className="entity-videos">
      <h2 className="section-title">Tout ce qui en est dit</h2>
      {!entity.appearances.length && <p className="muted">Aucune mention pour le moment.</p>}
      {entity.appearances.map(video => <article className="card entity-video" key={video.video_id}>
        <div className="spread"><Link className="entity-video-title" href={`/videos/${video.video_id}`}>{video.title}</Link><span className="field-hint">{new Date(video.created_at).toLocaleDateString("fr-FR")} · {formatDuration(video.duration_seconds).replace(/^00:/, "")}</span></div>
        <ul>{video.mentions.map((mention, index) => <li key={`${mention.start_seconds}-${index}`}><Link className="mono entity-time" href={`/videos/${video.video_id}?t=${Math.floor(mention.start_seconds)}`} title="Ouvrir la vidéo à ce moment">{formatDuration(mention.start_seconds)}</Link><span>{mention.context}</span></li>)}</ul>
      </article>)}
    </section>
  </div>;
}
