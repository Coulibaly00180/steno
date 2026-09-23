"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";

export type GlossarySuggestion = { term: string; variants: string[]; occurrences: number; videos: number };

const json = (body: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/**
 * Terms corrected by hand several times (n°2), offered for the global glossary in one click.
 * `refreshKey` reloads the list, e.g. after a correction of the transcript.
 */
export default function GlossarySuggestions({ refreshKey, onAccepted, compact = false }: {
  refreshKey?: unknown; onAccepted?: (terms: string[], term: string) => void; compact?: boolean;
}) {
  const [suggestions, setSuggestions] = useState<GlossarySuggestion[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [added, setAdded] = useState("");

  const load = useCallback(async () => {
    try { setSuggestions(await api<GlossarySuggestion[]>("/glossary/suggestions")); }
    catch { /* suggestions are a convenience: the glossary still works without them */ }
  }, []);
  useEffect(() => { void load(); }, [load, refreshKey]);

  async function accept(term: string) {
    setBusy(term); setError(""); setAdded("");
    try {
      const glossary = await api<{ terms: string[] }>("/glossary/suggestions/accept", json({ term }));
      setSuggestions(current => current.filter(item => item.term !== term));
      setAdded(term);
      onAccepted?.(glossary.terms, term);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(null); }
  }

  async function dismiss(term: string) {
    setBusy(term); setError("");
    try { await api("/glossary/suggestions/dismiss", json({ term })); setSuggestions(current => current.filter(item => item.term !== term)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(null); }
  }

  if (!suggestions.length && !added && !error) return null;
  const shown = compact ? suggestions.slice(0, 3) : suggestions;
  return <div className={`glossary-suggestions${compact ? " compact" : ""}`} aria-live="polite">
    {!!shown.length && <>
      <p className="field-label">{compact ? "À ajouter au glossaire ?" : "Suggestions tirées de vos corrections"}</p>
      <ul>{shown.map(item => <li key={item.term}>
        <span className="suggestion-term">{item.term}</span>
        <span className="field-hint">corrigé {item.occurrences} fois{item.videos > 1 ? ` dans ${item.videos} vidéos` : ""} · au lieu de {item.variants.map(variant => `« ${variant} »`).join(", ")}</span>
        <span className="row suggestion-actions"><button type="button" className="btn small primary" onClick={() => void accept(item.term)} disabled={busy !== null}>Ajouter</button><button type="button" className="btn small" onClick={() => void dismiss(item.term)} disabled={busy !== null}>Ignorer</button></span>
      </li>)}</ul>
    </>}
    {added && <p className="success-note" role="status">« {added} » ajouté au glossaire : il servira aux prochains imports.</p>}
    {error && <div className="error" role="alert">{error}</div>}
  </div>;
}
