"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { ActionRow, type ActionItem } from "./ActionsPanel";
import { Icon } from "./Icons";

type Meeting = { id: string; title: string; created_at: string; status: string; duration_seconds: number };
type Suggestion = { key: string; name: string; series_id: string | null; videos: { id: string; title: string; created_at: string }[] };
type VideoSeries = {
  series: { id: string; name: string; meetings: number; position: number } | null;
  previous: Meeting | null; next: Meeting | null; suggestion: Suggestion | null;
};
type SeriesItem = { id: string; name: string; meetings: number };
type Changes = {
  status: "none" | "first" | "no_summary" | "ready";
  series?: { id: string; name: string }; previous?: Meeting; open_actions?: ActionItem[];
  new?: string[]; changed?: string[]; dropped?: string[]; resolved?: (ActionItem & { evidence: string })[];
};

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
export const meetingDate = (value: string) => new Date(value).toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });

/** n°6: the meeting's series under its title, or an invitation to put it in one. */
export function SeriesBar({ videoId, onChanged }: { videoId: string; onChanged?: () => void }) {
  const [info, setInfo] = useState<VideoSeries | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [all, setAll] = useState<SeriesItem[]>([]);
  const [target, setTarget] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { setInfo(await api<VideoSeries>(`/videos/${videoId}/series`)); }
    catch (reason) { setError(String(reason)); }
  }, [videoId]);
  useEffect(() => { void load(); }, [load]);

  async function assign(seriesId: string | null) {
    setError("");
    try { setInfo(await api<VideoSeries>(`/videos/${videoId}/series`, json("PUT", { series_id: seriesId }))); setChoosing(false); onChanged?.(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  async function createSeries(seriesName: string, videoIds: string[]) {
    setError("");
    try { await api("/series", json("POST", { name: seriesName, video_ids: videoIds })); setChoosing(false); await load(); onChanged?.(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  async function openChooser() {
    setChoosing(true);
    try { const list = await api<SeriesItem[]>("/series"); setAll(list); setTarget(list[0]?.id ?? "new"); }
    catch (reason) { setError(String(reason)); }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (target === "new") { if (name.trim()) void createSeries(name.trim(), [videoId]); }
    else if (target) void assign(target);
  }

  if (!info) return null;
  const suggestion = info.suggestion;
  return <div className="series-bar">
    <Icon name="series" size={14} />
    {info.series ? <>
      <Link className="text-link" href={`/series/${info.series.id}`}>{info.series.name}</Link>
      <span className="field-hint">réunion {info.series.position} sur {info.series.meetings}</span>
      {info.previous && <Link className="series-nav" href={`/videos/${info.previous.id}`} title={info.previous.title}>← précédente</Link>}
      {info.next && <Link className="series-nav" href={`/videos/${info.next.id}`} title={info.next.title}>suivante →</Link>}
      <button type="button" className="link-button" onClick={() => void assign(null)}>retirer de la série</button>
    </> : choosing ? <form className="row series-chooser" onSubmit={submit}>
      <select value={target} onChange={event => setTarget(event.target.value)} aria-label="Série">
        {all.map(item => <option key={item.id} value={item.id}>{item.name} ({item.meetings})</option>)}
        <option value="new">Nouvelle série…</option>
      </select>
      {target === "new" && <input value={name} onChange={event => setName(event.target.value)} maxLength={120} placeholder="Ex. Comité budget" aria-label="Nom de la série" autoFocus required />}
      <button className="btn small primary">Ajouter</button><button type="button" className="btn small" onClick={() => setChoosing(false)}>Annuler</button>
    </form> : suggestion ? <>
      <span>Réunion récurrente ?</span>
      {suggestion.series_id
        ? <button type="button" className="btn small" onClick={() => void assign(suggestion.series_id)}>Ajouter à « {suggestion.name} »</button>
        : <button type="button" className="btn small" onClick={() => void createSeries(suggestion.name, suggestion.videos.map(video => video.id))} title={suggestion.videos.map(video => video.title).join("\n")}>Créer la série « {suggestion.name} » ({suggestion.videos.length} réunions)</button>}
      <button type="button" className="link-button" onClick={() => void openChooser()}>autre série</button>
    </> : <button type="button" className="link-button" onClick={() => void openChooser()}>Ajouter à une série de réunions</button>}
    {error && <span className="error inline-error">{error}</span>}
  </div>;
}

/** n°6: what changed since the previous meeting of the series, and the actions it left open. */
export function SeriesChanges({ videoId, refreshKey }: { videoId: string; refreshKey?: unknown }) {
  const [changes, setChanges] = useState<Changes | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<ActionItem[] | null>(null);

  const load = useCallback(async (refresh = false) => {
    setLoading(true); setError("");
    try {
      const result = await api<Changes>(`/videos/${videoId}/series/changes${refresh ? "?refresh=true" : ""}`);
      setChanges(result); setOpen(result.open_actions ?? null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setLoading(false); }
  }, [videoId]);
  useEffect(() => { void load(); }, [load, refreshKey]);

  // A box ticked here: the list is read again, without asking the model a second time.
  const reloadActions = useCallback(async () => {
    if (!changes?.series) return;
    try {
      const result = await api<{ items: ActionItem[] }>(`/actions?series_id=${changes.series.id}&kind=action&status=open`);
      const before = new Set((changes.open_actions ?? []).map(item => item.id));
      setOpen(result.items.filter(item => before.has(item.id)));
    } catch (reason) { setError(String(reason)); }
  }, [changes]);

  if (loading && !changes) return <section className="card series-changes"><h2>Depuis la dernière réunion</h2><p className="muted typing">Comparaison avec la réunion précédente…</p></section>;
  if (error && !changes) return <section className="card series-changes"><h2>Depuis la dernière réunion</h2><div className="error">{error}</div><button type="button" className="btn small" onClick={() => void load()}>Réessayer</button></section>;
  if (!changes || changes.status === "none") return null;
  const stillOpen = open ?? [];
  const openIds = new Set(stillOpen.map(item => item.id));
  const resolved = (changes.resolved ?? []).filter(item => openIds.has(item.id));
  const others = stillOpen.filter(item => !resolved.some(done => done.id === item.id));
  const section = (heading: string, items?: string[]) => items?.length ? <><h3 className="field-label">{heading}</h3><ul className="changes-list">{items.map(item => <li key={item}>{item}</li>)}</ul></> : null;

  return <section className="card series-changes">
    <div className="spread">
      <h2>Depuis la dernière réunion</h2>
      {changes.status === "ready" && <button type="button" className="btn small" onClick={() => void load(true)} disabled={loading} title="Comparer de nouveau les deux comptes-rendus">{loading ? "Comparaison…" : "Recomparer"}</button>}
    </div>
    {changes.status === "first" && <p className="muted">Première réunion de la série « {changes.series?.name} » : rien à comparer pour l&apos;instant.</p>}
    {changes.status === "no_summary" && <p className="muted">Cette réunion n&apos;a pas encore de résumé.</p>}
    {changes.status === "ready" && changes.previous && <>
      <p className="field-hint">Comparé au compte-rendu du <Link className="text-link" href={`/videos/${changes.previous.id}`}>{meetingDate(changes.previous.created_at)}</Link> ({changes.previous.title}). À relire : c&apos;est une lecture des deux résumés par le modèle.</p>
      {section("Nouveau", changes.new)}
      {section("A évolué", changes.changed)}
      {section("N'est plus mentionné", changes.dropped)}
      {!changes.new?.length && !changes.changed?.length && !changes.dropped?.length && <p className="muted">Aucun changement relevé entre les deux comptes-rendus.</p>}
    </>}
    {!!resolved.length && <><h3 className="field-label">Probablement faites depuis</h3><ul className="action-list">{resolved.map(item => <li key={item.id} className="resolved-action">
      <div className="action-body"><span className="action-text">{item.text}</span><span className="field-hint">{item.owner ? `${item.owner} · ` : ""}{item.video_title} — « {item.evidence} »</span></div>
      <button type="button" className="btn small primary" onClick={() => void api(`/actions/${item.id}`, json("PATCH", { status: "done" })).then(reloadActions).catch(reason => setError(String(reason)))}><Icon name="check" size={12} />Marquer faite</button>
    </li>)}</ul></>}
    {!!others.length && <><h3 className="field-label">Toujours ouvertes (réunions précédentes)</h3><ul className="action-list">{others.map(item => <ActionRow key={item.id} item={item} onChanged={() => void reloadActions()} showVideo />)}</ul></>}
    {changes.status !== "first" && !stillOpen.length && <p className="field-hint">Aucune action ouverte des réunions précédentes.</p>}
    {error && <div className="error" role="alert">{error}</div>}
  </section>;
}
