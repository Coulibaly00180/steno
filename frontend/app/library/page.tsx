"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "../../lib/api";
import { sourceLanguages } from "../../lib/analysis";
import { useVideoList } from "../../lib/library";
import { Icon } from "../../components/Icons";
import VideoTable from "../../components/VideoTable";

type TagCount = { name: string; count: number };

const statusOptions = [
  { value: "", label: "Tous les statuts" }, { value: "ACTIVE", label: "En cours ou en attente" },
  { value: "COMPLETED", label: "Terminés" }, { value: "FAILED", label: "En échec" }, { value: "CANCELLED", label: "Annulés" },
];
const periodOptions = [
  { value: "", label: "Toutes les dates" }, { value: "7", label: "7 derniers jours" },
  { value: "30", label: "30 derniers jours" }, { value: "365", label: "12 derniers mois" },
];
const SEARCH_DELAY_MS = 300;

export default function LibraryPage() {
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [status, setStatus] = useState("");
  const [language, setLanguage] = useState("");
  const [period, setPeriod] = useState("");
  const [tag, setTag] = useState("");
  const [tags, setTags] = useState<TagCount[]>([]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [search]);

  const query = useMemo(() => {
    const params = new URLSearchParams();
    if (debouncedSearch) params.set("q", debouncedSearch);
    if (status) params.set("status", status);
    if (language) params.set("language", language);
    if (tag) params.set("tag", tag);
    // Computed when the filter changes, not on every refresh: the list stays stable.
    if (period) params.set("created_after", new Date(Date.now() - Number(period) * 86_400_000).toISOString());
    return params.toString();
  }, [debouncedSearch, status, language, period, tag]);

  const { videos, error, reload } = useVideoList(query);

  async function loadTags() {
    try { setTags(await api<TagCount[]>("/tags")); } catch { /* the tag filter is optional */ }
  }
  useEffect(() => { void loadTags(); }, []);

  const filtered = !!(search.trim() || status || language || period || tag);
  function clearFilters() { setSearch(""); setDebouncedSearch(""); setStatus(""); setLanguage(""); setPeriod(""); setTag(""); }

  return <div className="page wide">
    <header className="page-header"><h1>Bibliothèque</h1><p>Retrouvez toutes vos analyses, leur progression et leurs résultats.</p></header>
    <div className="library-filters">
      <div className="library-search"><Icon name="search" size={14}/><input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Rechercher dans les titres, transcriptions et traductions…" aria-label="Rechercher dans la bibliothèque" maxLength={200} /></div>
      <select value={status} onChange={event => setStatus(event.target.value)} aria-label="Filtrer par statut">{statusOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
      <select value={language} onChange={event => setLanguage(event.target.value)} aria-label="Filtrer par langue parlée"><option value="">Toutes les langues</option>{sourceLanguages.map(option => <option key={option.code} value={option.code}>{option.label}</option>)}</select>
      <select value={period} onChange={event => setPeriod(event.target.value)} aria-label="Filtrer par date d'import">{periodOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
      <select value={tag} onChange={event => setTag(event.target.value)} aria-label="Filtrer par tag" disabled={!tags.length && !tag}><option value="">{tags.length || tag ? "Tous les tags" : "Aucun tag"}</option>{tag && !tags.some(item => item.name === tag) && <option value={tag}>{tag}</option>}{tags.map(item => <option key={item.name} value={item.name}>{item.name} ({item.count})</option>)}</select>
    </div>
    {error && <div className="error">{error}</div>}
    <div className="section-heading"><h2 className="section-title">{videos === null ? "Chargement…" : `${videos.length} analyse${videos.length > 1 ? "s" : ""}${filtered ? " trouvée" + (videos.length > 1 ? "s" : "") : ""}`}</h2><div className="row">{!!videos?.some(video => video.status === "COMPLETED") && <Link className="btn" href={`/ask${query ? `?${query}` : ""}`}><Icon name="chat" size={14}/>{filtered ? "Questions sur ces vidéos" : "Questions sur la bibliothèque"}</Link>}{filtered && <button className="btn" onClick={clearFilters}>Effacer les filtres</button>}<button className="btn" onClick={() => { void reload(); void loadTags(); }}>Actualiser</button></div></div>
    {videos !== null && <VideoTable videos={videos} onChanged={async () => { await reload(); await loadTags(); }} onTagClick={setTag} empty={filtered ? "Aucune analyse ne correspond à ces critères." : "Aucune analyse pour le moment."}/>}
  </div>;
}
