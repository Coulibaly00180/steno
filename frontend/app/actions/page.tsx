"use client";

import { useCallback, useEffect, useState } from "react";
import { API, api } from "../../lib/api";
import { Icon } from "../../components/Icons";
import { ActionRow, type ActionItem } from "../../components/ActionsPanel";

const statuses = [{ value: "open", label: "À faire" }, { value: "done", label: "Faites" }, { value: "dropped", label: "Abandonnées" }, { value: "", label: "Toutes" }];
const SEARCH_DELAY_MS = 250;

/** n°5: every action and decision of the library, the soonest deadlines first. */
export default function ActionsPage() {
  const [status, setStatus] = useState("open");
  const [kind, setKind] = useState("action");
  const [owner, setOwner] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<ActionItem[] | null>(null);
  const [owners, setOwners] = useState<string[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [search]);

  const load = useCallback(async () => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (kind) params.set("kind", kind);
    if (owner) params.set("owner", owner);
    if (query) params.set("q", query);
    try { const result = await api<{ items: ActionItem[]; owners: string[] }>(`/actions?${params}`); setItems(result.items); setOwners(result.owners); setError(""); }
    catch (reason) { setError(String(reason)); }
  }, [status, kind, owner, query]);
  useEffect(() => { void load(); }, [load]);

  const exportQuery = new URLSearchParams({ ...(status ? { status } : {}), ...(kind ? { kind } : {}) }).toString();
  return <div className="page wide">
    <header className="page-header"><h1>Actions et décisions</h1><p>Ce qui a été décidé, et ce qu&apos;il reste à faire, dans toutes vos réunions. Relevé à partir des résumés ; cochez, datez, réattribuez.</p></header>
    <div className="entity-filters">
      <div className="segmented" role="radiogroup" aria-label="Type">{[{ value: "action", label: "Actions" }, { value: "decision", label: "Décisions" }].map(option => <button type="button" role="radio" aria-checked={kind === option.value} className={kind === option.value ? "selected" : ""} key={option.value} onClick={() => setKind(option.value)}>{option.label}</button>)}</div>
      {kind === "action" && <select value={status} onChange={event => setStatus(event.target.value)} aria-label="Statut">{statuses.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select>}
      {kind === "action" && <select value={owner} onChange={event => setOwner(event.target.value)} aria-label="Responsable"><option value="">Tous les responsables</option>{owners.map(name => <option key={name} value={name}>{name}</option>)}</select>}
      <div className="library-search"><Icon name="search" size={14}/><input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Rechercher…" aria-label="Rechercher une action" maxLength={200} /></div>
      <a className="btn" href={`${API}/actions/export.csv?${exportQuery}`}><Icon name="download" size={14}/>CSV</a>
      {kind === "action" && <a className="btn" href={`${API}/actions/export.ics`} title="Les actions à faire et datées, dans votre agenda"><Icon name="download" size={14}/>Agenda .ics</a>}
    </div>
    {error && <div className="error">{error}</div>}
    {items !== null && !items.length && <div className="empty-state">{kind === "action" && status === "open" && !query && !owner ? "Rien à faire pour le moment." : "Aucun élément ne correspond."}</div>}
    {!!items?.length && <ul className="action-list card">{items.map(item => <ActionRow key={item.id} item={item} onChanged={() => void load()} showVideo />)}</ul>}
  </div>;
}
