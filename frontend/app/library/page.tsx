"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "../../lib/api";
import { sourceLanguages } from "../../lib/analysis";
import { useVideoList } from "../../lib/library";
import { Icon } from "../../components/Icons";
import VideoTable from "../../components/VideoTable";

type TagCount = { name: string; count: number };
type SavedSearch = { id: number; name: string; query: string };
type Filters = { search: string; status: string; language: string; period: string; tag: string; entity: string; entityName: string };

const statusOptions = [
  { value: "", label: "Tous les statuts" }, { value: "ACTIVE", label: "En cours ou en attente" },
  { value: "COMPLETED", label: "Terminés" }, { value: "FAILED", label: "En échec" }, { value: "CANCELLED", label: "Annulés" },
];
const periodOptions = [
  { value: "", label: "Toutes les dates" }, { value: "7", label: "7 derniers jours" },
  { value: "30", label: "30 derniers jours" }, { value: "365", label: "12 derniers mois" },
];
const SEARCH_DELAY_MS = 300;
const EMPTY: Filters = { search: "", status: "", language: "", period: "", tag: "", entity: "", entityName: "" };

/** The filters as they appear in the address and in a saved collection (n°17). */
function toQuery(filters: Filters): string {
  const params = new URLSearchParams();
  if (filters.search.trim()) params.set("q", filters.search.trim());
  if (filters.status) params.set("status", filters.status);
  if (filters.language) params.set("language", filters.language);
  if (filters.period) params.set("period", filters.period);
  if (filters.tag) params.set("tag", filters.tag);
  if (filters.entity) { params.set("entity", filters.entity); if (filters.entityName) params.set("entity_name", filters.entityName); }
  return params.toString();
}

function fromQuery(query: string): Filters {
  const params = new URLSearchParams(query);
  return {
    search: params.get("q") ?? "", status: params.get("status") ?? "", language: params.get("language") ?? "",
    period: params.get("period") ?? "", tag: params.get("tag") ?? "", entity: params.get("entity") ?? "", entityName: params.get("entity_name") ?? "",
  };
}

export default function LibraryPage() {
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [tags, setTags] = useState<TagCount[]>([]);
  const [collections, setCollections] = useState<SavedSearch[]>([]);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [ready, setReady] = useState(false);
  const { search, status, language, period, tag, entity, entityName } = filters;
  const set = (patch: Partial<Filters>) => setFilters(current => ({ ...current, ...patch }));

  // The address may carry filters: a link from an entity page, a reload, a shared link.
  useEffect(() => {
    const initial = fromQuery(window.location.search);
    setFilters(initial); setDebouncedSearch(initial.search.trim()); setReady(true);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [search]);

  const uiQuery = useMemo(() => toQuery({ ...filters, search: debouncedSearch }), [filters, debouncedSearch]);
  useEffect(() => {
    if (ready) window.history.replaceState(null, "", uiQuery ? `/library?${uiQuery}` : "/library");
  }, [uiQuery, ready]);

  const query = useMemo(() => {
    const params = new URLSearchParams();
    if (debouncedSearch) params.set("q", debouncedSearch);
    if (status) params.set("status", status);
    if (language) params.set("language", language);
    if (tag) params.set("tag", tag);
    if (entity) params.set("entity", entity);
    // Computed when the filter changes, not on every refresh: the list stays stable.
    if (period) params.set("created_after", new Date(Date.now() - Number(period) * 86_400_000).toISOString());
    return params.toString();
  }, [debouncedSearch, status, language, period, tag, entity]);

  const { videos, total, error, reload, loadMore, hasMore, loadingMore } = useVideoList(ready ? query : null);
  const count = total ?? videos?.length ?? 0;

  async function loadTags() {
    try { setTags(await api<TagCount[]>("/tags")); } catch { /* the tag filter is optional */ }
  }
  async function loadCollections() {
    try { setCollections(await api<SavedSearch[]>("/library/searches")); } catch { /* collections are optional */ }
  }
  useEffect(() => { void loadTags(); void loadCollections(); }, []);

  const filtered = !!uiQuery;
  function clearFilters() { setFilters(EMPTY); setDebouncedSearch(""); }

  function applyCollection(collection: SavedSearch) {
    const next = fromQuery(collection.query);
    setFilters(next); setDebouncedSearch(next.search.trim());
  }

  async function saveCollection(name: string) {
    setSaving(false);
    if (!name.trim()) return;
    try {
      await api("/library/searches", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name.trim(), query: uiQuery }) });
      await loadCollections(); setNotice(`Collection « ${name.trim()} » enregistrée.`);
    } catch (reason) { setNotice(reason instanceof Error ? reason.message : String(reason)); }
  }

  async function deleteCollection(collection: SavedSearch) {
    if (!window.confirm(`Supprimer la collection « ${collection.name} » ? Les vidéos ne sont pas touchées.`)) return;
    try { await api(`/library/searches/${collection.id}`, { method: "DELETE" }); await loadCollections(); }
    catch (reason) { setNotice(String(reason)); }
  }

  const activeCollection = collections.find(collection => collection.query === uiQuery);
  return <div className="page wide">
    <header className="page-header"><h1>Bibliothèque</h1><p>Retrouvez toutes vos analyses, leur progression et leurs résultats. La recherche trouve les mots exacts et les passages qui parlent du même sujet.</p></header>
    {!!collections.length && <div className="collections" aria-label="Collections">
      <span className="field-label">Collections</span>
      {collections.map(collection => <span className={`collection-chip${activeCollection?.id === collection.id ? " active" : ""}`} key={collection.id}>
        <button type="button" onClick={() => applyCollection(collection)} title={collection.query ? decodeURIComponent(collection.query.replace(/\+/g, " ")) : "Toute la bibliothèque"}>{collection.name}</button>
        <button type="button" className="collection-remove" aria-label={`Supprimer la collection « ${collection.name} »`} onClick={() => void deleteCollection(collection)}><Icon name="close" size={10}/></button>
      </span>)}
    </div>}
    <div className="library-filters">
      <div className="library-search"><Icon name="search" size={14}/><input type="search" value={search} onChange={event => set({ search: event.target.value })} placeholder="Rechercher un mot, un sujet, une question…" aria-label="Rechercher dans la bibliothèque" maxLength={200} /></div>
      <select value={status} onChange={event => set({ status: event.target.value })} aria-label="Filtrer par statut">{statusOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
      <select value={language} onChange={event => set({ language: event.target.value })} aria-label="Filtrer par langue parlée"><option value="">Toutes les langues</option>{sourceLanguages.map(option => <option key={option.code} value={option.code}>{option.label}</option>)}</select>
      <select value={period} onChange={event => set({ period: event.target.value })} aria-label="Filtrer par date d'import">{periodOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
      <select value={tag} onChange={event => set({ tag: event.target.value })} aria-label="Filtrer par tag" disabled={!tags.length && !tag}><option value="">{tags.length || tag ? "Tous les tags" : "Aucun tag"}</option>{tag && !tags.some(item => item.name === tag) && <option value={tag}>{tag}</option>}{tags.map(item => <option key={item.name} value={item.name}>{item.name} ({item.count})</option>)}</select>
    </div>
    {entity && <div className="active-filter"><span>Vidéos citant <Link className="text-link" href={`/entities/${entity}`}>{entityName || "cette fiche"}</Link></span><button type="button" className="icon-btn" aria-label="Retirer ce filtre" onClick={() => set({ entity: "", entityName: "" })}><Icon name="close" size={12}/></button></div>}
    {error && <div className="error">{error}</div>}
    {notice && <p className="success-note" role="status">{notice}</p>}
    <div className="section-heading"><h2 className="section-title">{videos === null ? "Chargement…" : `${count} analyse${count > 1 ? "s" : ""}${filtered ? " trouvée" + (count > 1 ? "s" : "") : ""}`}</h2><div className="row">
      {!!videos?.some(video => video.status === "COMPLETED") && <Link className="btn" href={`/ask${query ? `?${query}` : ""}`}><Icon name="chat" size={14}/>{filtered ? "Questions sur ces vidéos" : "Questions sur la bibliothèque"}</Link>}
      {filtered && !activeCollection && (saving
        ? <input className="rename-input" placeholder="Nom de la collection" maxLength={80} autoFocus aria-label="Nom de la collection" onBlur={event => void saveCollection(event.target.value)} onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); if (event.key === "Escape") setSaving(false); }} />
        : <button className="btn" onClick={() => { setSaving(true); setNotice(""); }}><Icon name="tag" size={14}/>Enregistrer cette recherche</button>)}
      {filtered && <button className="btn" onClick={clearFilters}>Effacer les filtres</button>}
      <button className="btn" onClick={() => { void reload(); void loadTags(); }}>Actualiser</button>
    </div></div>
    {videos !== null && <VideoTable videos={videos} onChanged={async () => { await reload(); await loadTags(); }} onTagClick={value => set({ tag: value })} empty={filtered ? "Aucune analyse ne correspond à ces critères." : "Aucune analyse pour le moment."}/>}
    {hasMore && <div className="load-more"><button type="button" className="btn" onClick={loadMore} disabled={loadingMore}>{loadingMore ? "Chargement…" : `Afficher plus (${videos?.length} sur ${count})`}</button></div>}
  </div>;
}
