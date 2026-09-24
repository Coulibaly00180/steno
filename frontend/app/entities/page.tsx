"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Icon } from "../../components/Icons";

export type EntityRow = { id: number; name: string; kind: string; kind_label: string; hidden: boolean; videos: number; mentions: number };
type Progress = { videos: number; ready: number; failed: number; waiting: number };

const entityKinds = [
  { value: "", label: "Tout" },
  { value: "person", label: "Personnes" },
  { value: "organization", label: "Organisations" },
  { value: "place", label: "Lieux" },
  { value: "date", label: "Dates" },
];
const SEARCH_DELAY_MS = 250;

export default function EntitiesPage() {
  const [kind, setKind] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [showHidden, setShowHidden] = useState(false);
  const [rows, setRows] = useState<EntityRow[] | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const params = new URLSearchParams();
    if (kind) params.set("kind", kind);
    if (query) params.set("q", query);
    if (showHidden) params.set("hidden", "true");
    api<EntityRow[]>(`/entities?${params}`).then(result => { setRows(result); setError(""); }).catch(reason => setError(String(reason)));
  }, [kind, query, showHidden]);

  useEffect(() => {
    api<Progress>("/entities/progress").then(setProgress).catch(() => setProgress(null));
  }, []);

  const reading = progress && progress.ready + progress.failed < progress.videos;
  return <div className="page wide">
    <header className="page-header"><h1>Personnes et dates</h1><p>Les personnes, organisations, lieux et dates cités dans vos vidéos. Ouvrez une fiche pour voir tout ce qui en est dit, vidéo par vidéo.</p></header>
    {reading && <div className="status-banner" role="status"><p>Relevé en cours en arrière-plan : {progress.ready} vidéo{progress.ready > 1 ? "s" : ""} lue{progress.ready > 1 ? "s" : ""} sur {progress.videos}. Il passe après les analyses et ne les ralentit pas.</p></div>}
    <div className="entity-filters">
      <div className="segmented" role="radiogroup" aria-label="Type">{entityKinds.map(option => <button type="button" role="radio" aria-checked={kind === option.value} className={kind === option.value ? "selected" : ""} key={option.value} onClick={() => setKind(option.value)}>{option.label}</button>)}</div>
      <div className="library-search"><Icon name="search" size={14}/><input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Rechercher un nom…" aria-label="Rechercher un nom" maxLength={120} /></div>
      <label className="checkbox"><input type="checkbox" checked={showHidden} onChange={event => setShowHidden(event.target.checked)} /><span>Afficher les fiches masquées</span></label>
    </div>
    {error && <div className="error">{error}</div>}
    {rows !== null && !rows.length && <div className="empty-state">{query || kind ? "Aucune fiche ne correspond." : "Aucune personne ni date relevée pour le moment."}</div>}
    {!!rows?.length && <ul className="entity-grid">{rows.map(row => <li key={row.id}>
      <Link href={`/entities/${row.id}`} className={`entity-card${row.hidden ? " hidden-entity" : ""}`}>
        <span className={`entity-kind kind-${row.kind}`}>{row.kind_label}</span>
        <strong>{row.name}</strong>
        <span className="field-hint">{row.videos} vidéo{row.videos > 1 ? "s" : ""} · {row.mentions} mention{row.mentions > 1 ? "s" : ""}{row.hidden ? " · masquée" : ""}</span>
      </Link>
    </li>)}</ul>}
  </div>;
}
