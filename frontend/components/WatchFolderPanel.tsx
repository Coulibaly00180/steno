"use client";

import { useCallback, useEffect, useState } from "react";
import { api, formatBytes } from "../lib/api";
import { sourceLanguages, sourcePolicies, summaryLengths, targetLanguages, type SourcePolicy, type SummaryLength } from "../lib/analysis";

type WatchSettings = {
  enabled: boolean; target_language: string | null; template_id: string | null; summary_length: SummaryLength;
  source_language: string | null; use_global_glossary: boolean; diarize: boolean; num_speakers: number | null;
  source_policy: SourcePolicy; tag: string | null;
};
type WatchState = {
  enabled: boolean; folder: string; stable_seconds: number;
  pending: { name: string; size_bytes: number }[];
  rejected: { name: string; size_bytes: number; reason: string; rejected_at: string }[];
};
type Template = { id: string; name: string; is_default: boolean };

const POLL_MS = 10_000;

export default function WatchFolderPanel() {
  const [config, setConfig] = useState<WatchSettings | null>(null);
  const [saved, setSaved] = useState("");
  const [state, setState] = useState<WatchState | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const loadState = useCallback(async () => {
    try { setState(await api<WatchState>("/watch-folder")); } catch { /* shown again at the next poll */ }
  }, []);
  useEffect(() => {
    api<WatchSettings>("/settings/watch-folder").then(value => { setConfig(value); setSaved(JSON.stringify(value)); }).catch(reason => setError(String(reason)));
    api<Template[]>("/templates").then(setTemplates).catch(() => setTemplates([]));
    void loadState();
  }, [loadState]);
  useEffect(() => {
    if (!state?.enabled) return;
    const timer = window.setInterval(() => void loadState(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [state?.enabled, loadState]);

  const dirty = !!config && JSON.stringify(config) !== saved;
  const update = (patch: Partial<WatchSettings>) => { setConfig(current => current && { ...current, ...patch }); setMessage(""); };

  async function save() {
    if (!config) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const value = await api<WatchSettings>("/settings/watch-folder", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config) });
      setConfig(value); setSaved(JSON.stringify(value));
      setMessage(value.enabled ? "Réglages enregistrés : les fichiers déposés dans le dossier seront importés avec ces options." : "Réglages enregistrés. Le dossier n'est pas surveillé.");
      await loadState();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  async function onRejected(name: string, action: "retry" | "delete") {
    if (action === "delete" && !window.confirm(`Supprimer définitivement « ${name} » ?`)) return;
    setError("");
    try {
      await api(`/watch-folder/rejected/${encodeURIComponent(name)}${action === "retry" ? "/retry" : ""}`, { method: action === "retry" ? "POST" : "DELETE" });
      await loadState();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  return <section className="card settings-section" id="dossier-surveille">
    <h2>Dossier surveillé</h2>
    <p className="muted">Déposez une vidéo ou un fichier audio dans le dossier <code>{state?.folder ?? "data/inbox"}</code> du projet : il est importé automatiquement dès que sa copie est terminée{state ? ` (taille inchangée pendant ${state.stable_seconds} s)` : ""}, avec les réglages ci-dessous. Le fichier est déplacé dans la bibliothèque, pas copié.</p>
    {config && <>
      <label className="checkbox watch-toggle"><input type="checkbox" checked={config.enabled} onChange={event => update({ enabled: event.target.checked })} /><span>Surveiller le dossier</span></label>
      <div className="form-grid">
        <div className="field"><label htmlFor="watch-language">Langue de traduction</label><select id="watch-language" value={config.target_language ?? ""} onChange={event => update({ target_language: event.target.value || null })}><option value="">Pas de traduction</option>{targetLanguages.map(language => <option key={language.value} value={language.value}>{language.label}</option>)}</select></div>
        <div className="field"><label htmlFor="watch-template">Template de résumé</label><select id="watch-template" value={config.template_id ?? ""} onChange={event => update({ template_id: event.target.value || null })}><option value="">Par défaut</option>{templates.map(template => <option key={template.id} value={template.id}>{template.name}</option>)}</select></div>
        <div className="field"><label htmlFor="watch-source">Langue parlée</label><select id="watch-source" value={config.source_language ?? ""} onChange={event => update({ source_language: event.target.value || null })}><option value="">Détection automatique</option>{sourceLanguages.map(language => <option key={language.code} value={language.code}>{language.label}</option>)}</select></div>
        <div className="field"><label htmlFor="watch-length">Longueur du résumé</label><select id="watch-length" value={config.summary_length} onChange={event => update({ summary_length: event.target.value as SummaryLength })}>{summaryLengths.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></div>
        <div className="field"><label htmlFor="watch-policy">Après l&apos;analyse</label><select id="watch-policy" value={config.source_policy} onChange={event => update({ source_policy: event.target.value as SourcePolicy })}>{sourcePolicies.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select><span className="field-hint">{sourcePolicies.find(option => option.value === config.source_policy)?.hint}</span></div>
        <div className="field"><label htmlFor="watch-tag">Tag ajouté <span className="muted">(facultatif)</span></label><input id="watch-tag" value={config.tag ?? ""} maxLength={40} onChange={event => update({ tag: event.target.value || null })} placeholder="Ex. Dossier surveillé" /></div>
      </div>
      <div className="field"><label className="checkbox"><input type="checkbox" checked={config.use_global_glossary} onChange={event => update({ use_global_glossary: event.target.checked })} /><span>Utiliser le glossaire global</span></label></div>
      <div className="field speakers-option"><label className="checkbox"><input type="checkbox" checked={config.diarize} onChange={event => update({ diarize: event.target.checked })} /><span>Identifier les intervenants</span></label>{config.diarize && <label className="speaker-count">Nombre d&apos;intervenants <input type="number" min={1} max={20} value={config.num_speakers ?? ""} onChange={event => update({ num_speakers: event.target.value ? Number(event.target.value) : null })} placeholder="auto" /></label>}</div>
      <div className="spread"><span className="field-hint">{dirty ? "Modifications non enregistrées" : ""}</span><button type="button" className="btn primary" onClick={() => void save()} disabled={!dirty || busy}>{busy ? "Enregistrement…" : "Enregistrer"}</button></div>
    </>}
    {message && <p className="success-note" role="status">{message}</p>}
    {error && <div className="error" role="alert">{error}</div>}
    {!!state?.pending.length && <div className="watch-list"><p className="field-label">En attente dans le dossier</p><ul>{state.pending.map(file => <li key={file.name}><span>{file.name}</span><span className="field-hint">{formatBytes(file.size_bytes)}{state.enabled ? " · copie en cours ou import imminent" : " · la surveillance est désactivée"}</span></li>)}</ul></div>}
    {!!state?.rejected.length && <div className="watch-list"><p className="field-label">Fichiers refusés (rangés dans <code>{state.folder}/_rejets</code>)</p><ul>{state.rejected.map(file => <li key={file.name}><span>{file.name}</span><span className="field-hint">{file.reason}</span><span className="row"><button type="button" className="btn small" onClick={() => void onRejected(file.name, "retry")}>Réessayer</button><button type="button" className="btn small danger" onClick={() => void onRejected(file.name, "delete")}>Supprimer</button></span></li>)}</ul></div>}
  </section>;
}
