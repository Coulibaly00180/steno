"use client";

import { useState, type FormEvent } from "react";
import { api } from "../lib/api";

/** Fix a recurring misrecognition (a name, an acronym) across the whole transcript. */
export default function ReplaceBar({ videoId, disabled, onReplaced }: { videoId: string; disabled: boolean; onReplaced: (message: string) => Promise<void> }) {
  const [find, setFind] = useState("");
  const [replace, setReplace] = useState("");
  const [matchCase, setMatchCase] = useState(false);
  const [wholeWord, setWholeWord] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault(); if (!find) return;
    setBusy(true); setError("");
    try {
      const result = await api<{ replaced: number; segments: number }>(`/videos/${videoId}/transcript/replace`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ find, replace, match_case: matchCase, whole_word: wholeWord }) });
      await onReplaced(result.replaced ? `${result.replaced} remplacement${result.replaced > 1 ? "s" : ""} dans ${result.segments} ligne${result.segments > 1 ? "s" : ""}` : `« ${find} » introuvable`);
      if (result.replaced) { setFind(""); setReplace(""); }
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  }

  return <form className="replace-bar" onSubmit={submit}>
    <input value={find} onChange={event => setFind(event.target.value)} maxLength={200} placeholder="Remplacer…" aria-label="Texte à remplacer" disabled={disabled} />
    <input value={replace} onChange={event => setReplace(event.target.value)} maxLength={200} placeholder="par…" aria-label="Remplacer par" disabled={disabled} />
    <label className="checkbox"><input type="checkbox" checked={wholeWord} onChange={event => setWholeWord(event.target.checked)} disabled={disabled} /><span>Mot entier</span></label>
    <label className="checkbox"><input type="checkbox" checked={matchCase} onChange={event => setMatchCase(event.target.checked)} disabled={disabled} /><span>Respecter la casse</span></label>
    <button className="btn" disabled={disabled || busy || !find}>{busy ? "…" : "Tout remplacer"}</button>
    {error && <span className="error inline-error" role="alert">{error}</span>}
  </form>;
}
