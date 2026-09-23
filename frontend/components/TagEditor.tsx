"use client";

import { useEffect, useState, type KeyboardEvent } from "react";
import { api } from "../lib/api";
import { Icon } from "./Icons";

const TAG_MAX_CHARS = 40;
const TAGS_PER_VIDEO = 20;

/** Tags of one video, saved on every change (n°17). */
export default function TagEditor({ videoId, tags, onSaved }: { videoId: string; tags: string[]; onSaved: (tags: string[]) => void }) {
  const [draft, setDraft] = useState("");
  const [known, setKnown] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api<{ name: string }[]>("/tags").then(rows => setKnown(rows.map(row => row.name))).catch(() => setKnown([]));
  }, [tags]);

  async function save(next: string[]) {
    setSaving(true); setError("");
    try {
      const saved = await api<{ tags: string[] }>(`/videos/${videoId}/tags`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tags: next }) });
      onSaved(saved.tags); setDraft("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setSaving(false); }
  }

  function add() {
    const names = draft.split(",").map(name => name.trim().replace(/\s+/g, " ")).filter(Boolean);
    if (!names.length) return;
    void save([...tags, ...names]);
  }

  function keyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") { event.preventDefault(); add(); }
    else if (event.key === "Backspace" && !draft && tags.length) void save(tags.slice(0, -1));
  }

  const suggestions = known.filter(name => !tags.some(tag => tag.toLowerCase() === name.toLowerCase()));

  return <div className="tag-editor">
    <Icon name="tag" size={14} aria-hidden="true"/>
    {tags.map(tag => <span className="tag-chip" key={tag}>{tag}<button type="button" aria-label={`Retirer le tag ${tag}`} onClick={() => void save(tags.filter(item => item !== tag))} disabled={saving}><Icon name="close" size={10}/></button></span>)}
    {tags.length < TAGS_PER_VIDEO && <input value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={keyDown} onBlur={() => { if (draft.trim()) add(); }} placeholder={tags.length ? "Ajouter…" : "Ajouter un tag (client, projet…)"} aria-label="Ajouter un tag" maxLength={TAG_MAX_CHARS} list={`tags-${videoId}`} disabled={saving}/>}
    <datalist id={`tags-${videoId}`}>{suggestions.map(name => <option key={name} value={name}/>)}</datalist>
    {error && <span className="tag-error" role="alert">{error}</span>}
  </div>;
}
