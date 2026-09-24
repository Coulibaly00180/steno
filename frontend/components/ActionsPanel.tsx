"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { API, api, formatDuration } from "../lib/api";
import { Icon } from "./Icons";

export type ActionItem = {
  id: string; video_id: string; video_title?: string | null; kind: "action" | "decision"; text: string; owner: string | null;
  due_text: string | null; due_date: string | null; status: "open" | "done" | "dropped"; start_seconds: number | null;
  source: "auto" | "manual"; edited: boolean;
};

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export function dueLabel(item: ActionItem) {
  if (!item.due_date) return item.due_text || "";
  const due = new Date(`${item.due_date}T12:00:00`);
  const days = Math.round((due.getTime() - Date.now()) / 86_400_000);
  const date = due.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
  if (item.status !== "open") return date;
  return days < 0 ? `${date} · en retard` : days === 0 ? `${date} · aujourd'hui` : days <= 7 ? `${date} · dans ${days} j` : date;
}

/** One line of an action or decision, edited in place (shared with the library-wide page). */
export function ActionRow({ item, onChanged, onSeek, showVideo = false }: {
  item: ActionItem; onChanged: () => void; onSeek?: (seconds: number) => void; showVideo?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(item.text);
  const [owner, setOwner] = useState(item.owner ?? "");
  const [due, setDue] = useState(item.due_date ?? "");
  const [error, setError] = useState("");

  async function patch(change: Record<string, unknown>) {
    setError("");
    try { await api(`/actions/${item.id}`, json("PATCH", change)); onChanged(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    await patch({ text: text.trim(), owner: owner.trim(), due_date: due || null });
    setEditing(false);
  }

  async function remove() {
    if (!window.confirm(`Supprimer « ${item.text} » ?`)) return;
    try { await api(`/actions/${item.id}`, { method: "DELETE" }); onChanged(); }
    catch (reason) { setError(String(reason)); }
  }

  const late = item.status === "open" && item.due_date && new Date(`${item.due_date}T23:59:59`) < new Date();
  if (editing) return <li className="action-row editing"><form onSubmit={save}>
    <input value={text} onChange={event => setText(event.target.value)} maxLength={400} aria-label="Intitulé" autoFocus required />
    <input value={owner} onChange={event => setOwner(event.target.value)} maxLength={80} placeholder="Responsable" aria-label="Responsable" />
    <input type="date" value={due} onChange={event => setDue(event.target.value)} aria-label="Échéance" />
    <button className="btn small primary">Enregistrer</button><button type="button" className="btn small" onClick={() => setEditing(false)}>Annuler</button>
  </form>{error && <span className="error inline-error">{error}</span>}</li>;

  return <li className={`action-row status-${item.status}`}>
    {item.kind === "action"
      ? <input type="checkbox" checked={item.status === "done"} onChange={event => void patch({ status: event.target.checked ? "done" : "open" })} aria-label={item.status === "done" ? "Marquer à faire" : "Marquer fait"} />
      : <span className="decision-mark" aria-hidden="true">◆</span>}
    <div className="action-body">
      <span className="action-text">{item.text}</span>
      <span className="field-hint">
        {[item.owner && <strong key="o">{item.owner}</strong>, dueLabel(item) && <span key="d" className={late ? "late" : undefined}>{dueLabel(item)}</span>,
          item.status === "dropped" && <span key="s">abandonné</span>,
          showVideo && item.video_title && <a key="v" className="text-link" href={`/videos/${item.video_id}${item.start_seconds != null ? `?t=${Math.floor(item.start_seconds)}` : ""}`}>{item.video_title}</a>,
          !showVideo && item.start_seconds != null && onSeek && <button key="t" type="button" className="timestamp-link mono" onClick={() => onSeek(item.start_seconds!)}>{formatDuration(item.start_seconds)}</button>,
          item.source === "manual" && <span key="m">ajoutée à la main</span>,
        ].filter(Boolean).reduce<React.ReactNode[]>((all, part, index) => index ? [...all, " · ", part] : [part], [])}
      </span>
      {error && <span className="error inline-error">{error}</span>}
    </div>
    <span className="row action-tools">
      {item.kind === "action" && item.status !== "done" && <button type="button" className="icon-btn small-icon" title={item.status === "dropped" ? "Reprendre" : "Abandonner"} onClick={() => void patch({ status: item.status === "dropped" ? "open" : "dropped" })}>{item.status === "dropped" ? "↺" : "✕"}</button>}
      <button type="button" className="icon-btn small-icon" title="Modifier" onClick={() => setEditing(true)}><Icon name="edit" size={12} /></button>
      <button type="button" className="icon-btn small-icon" title="Supprimer" onClick={() => void remove()}><Icon name="trash" size={12} /></button>
    </span>
  </li>;
}

/** n°5: the video's actions and decisions, below its summary. */
export default function ActionsPanel({ videoId, onSeek, refreshKey, busy }: {
  videoId: string; onSeek?: (seconds: number) => void; refreshKey?: unknown; busy: boolean;
}) {
  const [items, setItems] = useState<ActionItem[] | null>(null);
  const [adding, setAdding] = useState<"action" | "decision" | null>(null);
  const [text, setText] = useState("");
  const [owner, setOwner] = useState("");
  const [due, setDue] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try { setItems(await api<ActionItem[]>(`/videos/${videoId}/actions`)); }
    catch (reason) { setError(String(reason)); }
  }, [videoId]);
  useEffect(() => { void load(); }, [load, refreshKey]);

  async function add(event: FormEvent) {
    event.preventDefault();
    if (!adding || !text.trim()) return;
    setError("");
    try {
      await api(`/videos/${videoId}/actions`, json("POST", { kind: adding, text: text.trim(), owner: owner.trim() || null, due_date: due || null }));
      setText(""); setOwner(""); setDue(""); setAdding(null); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  async function reread() {
    setWorking(true); setError(""); setMessage("");
    try { const result = await api<{ added: number }>(`/videos/${videoId}/actions/extract`, { method: "POST" }); setMessage(`${result.added} élément${result.added > 1 ? "s" : ""} relevé${result.added > 1 ? "s" : ""} dans le résumé.`); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setWorking(false); }
  }

  if (items === null) return null;
  const actions = items.filter(item => item.kind === "action");
  const decisions = items.filter(item => item.kind === "decision");
  const dated = actions.some(item => item.due_date && item.status === "open");
  return <section className="card actions-panel">
    <div className="spread">
      <h2>Actions et décisions</h2>
      <div className="row">
        <button type="button" className="btn small" onClick={() => void reread()} disabled={working || busy} title="Relire le résumé actuel ; ce que vous avez modifié est conservé">{working ? "Lecture…" : "Relire le résumé"}</button>
        {!!items.length && <a className="btn small" href={`${API}/actions/export.csv?video_id=${videoId}`}><Icon name="download" size={12} />CSV</a>}
        {dated && <a className="btn small" href={`${API}/actions/export.ics?video_id=${videoId}`} title="Les échéances dans votre agenda (Outlook, Google Agenda…)"><Icon name="download" size={12} />Agenda .ics</a>}
      </div>
    </div>
    {!items.length && <p className="muted">Aucune action ni décision relevée dans le résumé.</p>}
    {!!actions.length && <><h3 className="field-label">Actions</h3><ul className="action-list">{actions.map(item => <ActionRow key={item.id} item={item} onChanged={() => void load()} onSeek={onSeek} />)}</ul></>}
    {!!decisions.length && <><h3 className="field-label">Décisions</h3><ul className="action-list">{decisions.map(item => <ActionRow key={item.id} item={item} onChanged={() => void load()} onSeek={onSeek} />)}</ul></>}
    {adding
      ? <form className="action-add" onSubmit={add}>
        <input value={text} onChange={event => setText(event.target.value)} maxLength={400} placeholder={adding === "action" ? "Ex. Envoyer le devis à Acme" : "Ex. Le budget marketing est validé"} aria-label="Intitulé" autoFocus required />
        {adding === "action" && <><input value={owner} onChange={event => setOwner(event.target.value)} maxLength={80} placeholder="Responsable" aria-label="Responsable" /><input type="date" value={due} onChange={event => setDue(event.target.value)} aria-label="Échéance" /></>}
        <button className="btn small primary">Ajouter</button><button type="button" className="btn small" onClick={() => setAdding(null)}>Annuler</button>
      </form>
      : <div className="row action-add-buttons"><button type="button" className="btn small" onClick={() => setAdding("action")}>+ Action</button><button type="button" className="btn small" onClick={() => setAdding("decision")}>+ Décision</button></div>}
    {message && <p className="success-note" role="status">{message}</p>}
    {error && <div className="error" role="alert">{error}</div>}
  </section>;
}
