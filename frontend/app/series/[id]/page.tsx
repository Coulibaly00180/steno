"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useParams, useRouter } from "next/navigation";
import { API, api, formatDuration } from "../../../lib/api";
import { Icon } from "../../../components/Icons";
import { ActionRow, type ActionItem } from "../../../components/ActionsPanel";
import { meetingDate } from "../../../components/SeriesPanel";

type Detail = {
  id: string; name: string; created_at: string;
  meetings: { id: string; title: string; created_at: string; status: string; duration_seconds: number }[];
  open_actions: ActionItem[]; done_actions: number; decisions: ActionItem[];
};

/** n°6: one series — its meetings in order, the actions still open, the decisions taken. */
export default function SeriesDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { const loaded = await api<Detail>(`/series/${id}`); setDetail(loaded); setName(loaded.name); setError(""); }
    catch (reason) { setError(String(reason)); }
  }, [id]);
  useEffect(() => { void load(); }, [load]);

  async function rename(event: FormEvent) {
    event.preventDefault();
    try { await api(`/series/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name.trim() }) }); setRenaming(false); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  async function remove() {
    if (!window.confirm("Supprimer cette série ? Les réunions restent dans la bibliothèque.")) return;
    try { await api(`/series/${id}`, { method: "DELETE" }); router.push("/series"); }
    catch (reason) { setError(String(reason)); }
  }

  if (!detail) return <div className="page">{error ? <div className="error">{error}</div> : <p className="muted">Chargement…</p>}</div>;
  const titles = new Map(detail.meetings.map(meeting => [meeting.id, meeting]));
  return <div className="page wide">
    <header className="detail-header"><div>
      <p className="eyebrow"><Link className="text-link" href="/series">Séries de réunions</Link></p>
      {renaming ? <form className="row" onSubmit={rename}><input value={name} onChange={event => setName(event.target.value)} maxLength={120} aria-label="Nom de la série" autoFocus required /><button className="btn small primary">Enregistrer</button><button type="button" className="btn small" onClick={() => setRenaming(false)}>Annuler</button></form>
        : <h1 className="detail-title">{detail.name}</h1>}
      <div className="meta"><span className="pill">{detail.meetings.length} réunion{detail.meetings.length > 1 ? "s" : ""}</span><span className="pill">{detail.open_actions.length} action{detail.open_actions.length > 1 ? "s" : ""} ouverte{detail.open_actions.length > 1 ? "s" : ""}</span><span className="pill">{detail.done_actions ? `${detail.done_actions} faite${detail.done_actions > 1 ? "s" : ""}` : "aucune faite"}</span></div>
    </div><div className="row">
      {!renaming && <button className="btn" onClick={() => setRenaming(true)}><Icon name="edit" size={14} />Renommer</button>}
      {!!detail.open_actions.length && <a className="btn" href={`${API}/actions/export.ics`} title="Les actions datées, dans votre agenda"><Icon name="download" size={14} />Agenda .ics</a>}
      <button className="btn danger" onClick={() => void remove()}><Icon name="trash" size={14} />Supprimer</button>
    </div></header>
    {error && <div className="error" role="alert">{error}</div>}

    <div className="series-layout">
      <section className="card">
        <h2>Réunions</h2>
        {!detail.meetings.length ? <p className="muted">Aucune réunion : ajoutez-en depuis leur page.</p>
          : <ol className="series-timeline">{detail.meetings.map(meeting => <li key={meeting.id}>
            <span className="field-hint">{meetingDate(meeting.created_at)}</span>
            <Link className="text-link" href={`/videos/${meeting.id}`}>{meeting.title}</Link>
            <span className="field-hint mono">{formatDuration(meeting.duration_seconds)}{meeting.status !== "COMPLETED" ? " · en cours de traitement" : ""}</span>
          </li>)}</ol>}
      </section>
      <div>
        <section className="card">
          <h2>Actions ouvertes</h2>
          {!detail.open_actions.length ? <p className="muted">Toutes les actions de la série sont faites ou abandonnées.</p>
            : <ul className="action-list">{detail.open_actions.map(item => <ActionRow key={item.id} item={{ ...item, video_title: titles.get(item.video_id)?.title ?? item.video_title }} onChanged={() => void load()} showVideo />)}</ul>}
        </section>
        <section className="card">
          <h2>Décisions</h2>
          {!detail.decisions.length ? <p className="muted">Aucune décision relevée.</p>
            : <ol className="series-decisions">{detail.decisions.map(item => <li key={item.id}>
              <span>{item.text}</span>
              <span className="field-hint">{titles.get(item.video_id) ? meetingDate(titles.get(item.video_id)!.created_at) : ""}</span>
            </li>)}</ol>}
        </section>
      </div>
    </div>
  </div>;
}
