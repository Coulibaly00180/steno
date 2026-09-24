"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../../lib/api";
import { Icon } from "../../components/Icons";
import { meetingDate } from "../../components/SeriesPanel";

type Series = { id: string; name: string; meetings: number; last_meeting_at: string | null; open_actions: number };
type Suggestion = { key: string; name: string; series_id: string | null; videos: { id: string; title: string; created_at: string }[] };

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function SuggestionCard({ suggestion, series, onDone }: { suggestion: Suggestion; series: Series[]; onDone: () => void }) {
  const [name, setName] = useState(suggestion.name);
  const [error, setError] = useState("");
  const target = series.find(item => item.id === suggestion.series_id);

  async function accept(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      if (target) {
        for (const video of suggestion.videos) await api(`/videos/${video.id}/series`, json("PUT", { series_id: target.id }));
      } else {
        await api("/series", json("POST", { name: name.trim(), video_ids: suggestion.videos.map(video => video.id) }));
      }
      onDone();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  return <form className="card series-suggestion" onSubmit={accept}>
    <ul className="field-hint">{suggestion.videos.map(video => <li key={video.id}><Link className="text-link" href={`/videos/${video.id}`}>{video.title}</Link> · {meetingDate(video.created_at)}</li>)}</ul>
    <div className="row">
      {target ? <span>Ces réunions ressemblent à la série « {target.name} ».</span>
        : <input value={name} onChange={event => setName(event.target.value)} maxLength={120} aria-label="Nom de la série" required />}
      <button className="btn small primary">{target ? "Les y ajouter" : "Créer la série"}</button>
    </div>
    {error && <span className="error inline-error">{error}</span>}
  </form>;
}

/** n°6: the series of meetings, and the recurring meetings not grouped yet. */
export default function SeriesPage() {
  const [series, setSeries] = useState<Series[] | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [list, found] = await Promise.all([api<Series[]>("/series"), api<Suggestion[]>("/series/suggestions")]);
      setSeries(list); setSuggestions(found); setError("");
    } catch (reason) { setError(String(reason)); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function create(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    try { await api("/series", json("POST", { name: name.trim() })); setName(""); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  return <div className="page wide">
    <header className="page-header"><h1>Séries de réunions</h1><p>Regroupez les réunions qui se suivent (comité hebdomadaire, point projet) : les actions encore ouvertes d&apos;une réunion à l&apos;autre, les décisions dans l&apos;ordre, et ce qui a changé depuis la dernière fois.</p></header>
    {error && <div className="error" role="alert">{error}</div>}
    {series !== null && !series.length && !suggestions.length && <div className="empty-state">Aucune série pour le moment. Sur la page d&apos;une réunion, « Ajouter à une série de réunions ».</div>}
    {!!series?.length && <div className="series-grid">{series.map(item => <Link key={item.id} href={`/series/${item.id}`} className="card series-card">
      <strong><Icon name="series" size={15} /> {item.name}</strong>
      <span className="field-hint">{item.meetings} réunion{item.meetings > 1 ? "s" : ""}{item.last_meeting_at ? ` · dernière le ${meetingDate(item.last_meeting_at)}` : ""}</span>
      <span className={item.open_actions ? "series-open" : "field-hint"}>{item.open_actions ? `${item.open_actions} action${item.open_actions > 1 ? "s" : ""} ouverte${item.open_actions > 1 ? "s" : ""}` : "Aucune action ouverte"}</span>
    </Link>)}</div>}
    {!!suggestions.length && <section className="settings-section">
      <h2>Réunions qui semblent se répéter</h2>
      <p className="muted">Même titre, dates et numéros mis à part.</p>
      {suggestions.map(suggestion => <SuggestionCard key={suggestion.key} suggestion={suggestion} series={series ?? []} onDone={() => void load()} />)}
    </section>}
    <form className="row series-create" onSubmit={create}>
      <input value={name} onChange={event => setName(event.target.value)} maxLength={120} placeholder="Nouvelle série (ex. Comité budget)" aria-label="Nom de la nouvelle série" />
      <button className="btn" disabled={!name.trim()}>Créer</button>
    </form>
  </div>;
}
