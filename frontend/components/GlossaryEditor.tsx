"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { GLOSSARY_MAX_TERMS, splitTerms } from "../lib/analysis";

const LEAVE_MESSAGE = "Le glossaire a été modifié sans être enregistré. Quitter la page ?";

export default function GlossaryEditor() {
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    api<{ terms: string[] }>("/glossary")
      .then(glossary => { const value = glossary.terms.join("\n"); setText(value); setSaved(value); setLoaded(true); })
      .catch(reason => setError(String(reason)));
  }, []);

  const dirty = loaded && text !== saved;
  const count = splitTerms(text).length;

  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    // In-app navigation does not fire beforeunload: ask before following a link.
    const click = (event: MouseEvent) => {
      const link = (event.target as HTMLElement | null)?.closest("a[href]");
      if (link && !window.confirm(LEAVE_MESSAGE)) { event.preventDefault(); event.stopPropagation(); }
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", click, true);
    return () => { window.removeEventListener("beforeunload", beforeUnload); document.removeEventListener("click", click, true); };
  }, [dirty]);

  async function save() {
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await api<{ terms: string[] }>("/glossary", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ terms: text.split("\n") }) });
      const value = result.terms.join("\n");
      setText(value); setSaved(value);
      setMessage("Glossaire enregistré. Les modifications s'appliquent aux prochains imports.");
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  }

  return <section className="card glossary-card">
    <h2>Glossaire global</h2>
    <p className="muted">Noms de votre équipe, de vos clients, sigles et jargon : ils sont transmis à la transcription et au résumé de chaque nouvel import (case « Utiliser le glossaire global »). Réservez-le aux noms propres et aux termes ambigus : seuls les premiers termes tiennent dans la transcription.</p>
    <label htmlFor="glossary" className="field-label">Un terme par ligne</label>
    <textarea id="glossary" rows={8} value={text} onChange={event => { setText(event.target.value); setMessage(""); }} disabled={!loaded} placeholder={"OKR\nDoñana\nKubernetes"} />
    <div className="spread glossary-footer"><span className={`field-hint${count > GLOSSARY_MAX_TERMS ? " over-limit" : ""}`}>{count} / {GLOSSARY_MAX_TERMS} termes{dirty ? " · modifications non enregistrées" : ""}</span><button className="btn primary" onClick={save} disabled={!dirty || busy || count > GLOSSARY_MAX_TERMS}>{busy ? "Enregistrement…" : "Enregistrer"}</button></div>
    {error && <div className="error" role="alert">{error}</div>}
    {message && <p className="success-note" role="status">{message}</p>}
  </section>;
}
