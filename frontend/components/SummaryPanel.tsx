"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api, formatDuration } from "../lib/api";
import { CUSTOM_PROMPT_MAX_CHARS, summaryLengthLabels, summaryLengths, wordBudget, type SummaryLength } from "../lib/analysis";
import { Icon } from "./Icons";
import TimestampText from "./TimestampText";

export type Summary = { id: string; content_markdown: string; model: string; language?: string; template_id?: string | null; template_name?: string | null; summary_length?: string | null; created_at: string; edited_at?: string | null };
type Template = { id: string; name: string; is_default: boolean };
// n°3: the passage behind a line of the summary.
type LineSource = { line: number; start_seconds: number; end_seconds: number; distance: number; supported: boolean; excerpt: string; cited_seconds: number | null; cited_matches: boolean | null };
type Sources = { status: "ready" | "indexing"; lines: LineSource[] };

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function versionLabel(summary: Summary) {
  const date = new Date(summary.created_at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
  const length = summary.summary_length ? summaryLengthLabels[summary.summary_length] || summary.summary_length : "";
  return [date, length, summary.template_name || (summary.template_id ? "template supprimé" : "")].filter(Boolean).join(" · ");
}

export default function SummaryPanel({ videoId, summaries, duration, busy, outdated, onSeek, onChanged, onJobStarted }: {
  videoId: string;
  summaries: Summary[];
  duration: number;
  /** A job is running: edits and regeneration wait for it. */
  busy: boolean;
  outdated: boolean;
  onSeek?: (seconds: number) => void;
  onChanged: () => Promise<void>;
  onJobStarted: (jobId: string) => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [regenerating, setRegenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [sources, setSources] = useState<Sources | null>(null);
  const [showSources, setShowSources] = useState(true);

  // A new version appears after a regeneration: show it.
  const latest = summaries.at(-1);
  useEffect(() => { setSelectedId(null); }, [latest?.id]);
  const summary = summaries.find(item => item.id === selectedId) ?? latest;

  useEffect(() => {
    setSources(null);
    if (!summary || editing) return;
    let cancelled = false;
    api<Sources>(`/videos/${videoId}/summaries/${summary.id}/sources`).then(result => { if (!cancelled) setSources(result); }).catch(() => { if (!cancelled) setSources(null); });
    return () => { cancelled = true; };
  }, [videoId, summary?.id, summary?.content_markdown, editing]);
  const byLine = new Map((sources?.lines ?? []).map(source => [source.line, source]));
  const unchecked = (sources?.lines ?? []).filter(source => !source.supported).length;

  async function save() {
    if (!summary) return;
    setSaving(true); setError("");
    try { await api(`/videos/${videoId}/summaries/${summary.id}`, json("PUT", { content_markdown: draft })); setEditing(false); await onChanged(); }
    catch (reason) { setError(String(reason)); }
    finally { setSaving(false); }
  }

  if (!summary) return <div className="content-panel">Résumé en cours…</div>;

  return <section className="summary-panel">
    <div className="summary-toolbar">
      {summaries.length > 1
        ? <div className="field-control version-select"><select aria-label="Version du résumé" value={summary.id} onChange={event => { setSelectedId(event.target.value); setEditing(false); }}>{[...summaries].reverse().map((item, index) => <option key={item.id} value={item.id}>{index === 0 ? "Dernière version" : "Version"} · {versionLabel(item)}</option>)}</select><Icon name="chevron" size={14}/></div>
        : <p className="summary-meta">{summary.summary_length && <>Résumé {summaryLengthLabels[summary.summary_length] || summary.summary_length} · ~{wordBudget(duration, summary.summary_length as SummaryLength)} mots · </>}{summary.template_name ? `Template « ${summary.template_name} »` : summary.template_id ? "Template supprimé" : "Template par défaut"}{summary.edited_at && " · corrigé à la main"}</p>}
      <div className="row">
        {sources?.status === "ready" && !editing && <label className="checkbox sources-toggle"><input type="checkbox" checked={showSources} onChange={event => setShowSources(event.target.checked)} /><span>Sources{unchecked ? ` · ${unchecked} à vérifier` : ""}</span></label>}
        {!editing && <button className="btn" onClick={() => { setDraft(summary.content_markdown); setEditing(true); }} disabled={busy} title={busy ? "Disponible à la fin du traitement en cours" : undefined}><Icon name="edit" size={14}/>Corriger</button>}
        <button className="btn" onClick={() => setRegenerating(true)} disabled={busy || editing} title={busy ? "Disponible à la fin du traitement en cours" : undefined}><Icon name="sparkle" size={14}/>Régénérer</button>
      </div>
    </div>
    {outdated && summary.id === latest?.id && <div className="status-banner" role="status"><p>La transcription a été corrigée après ce résumé. <button className="text-link link-button" onClick={() => setRegenerating(true)} disabled={busy}>Régénérer le résumé</button></p></div>}
    {error && <div className="error" role="alert">{error}</div>}
    {editing
      ? <div className="summary-edit"><textarea value={draft} onChange={event => setDraft(event.target.value)} rows={18} aria-label="Contenu du résumé (Markdown)" /><div className="row"><button className="btn primary" onClick={save} disabled={saving || !draft.trim()}>{saving ? "Enregistrement…" : "Enregistrer"}</button><button className="btn" onClick={() => setEditing(false)} disabled={saving}>Annuler</button><span className="field-hint">Les exports sont mis à jour à l&apos;enregistrement.</span></div></div>
      : showSources && sources?.status === "ready"
        ? <div className="content-panel summary-content with-sources">{summary.content_markdown.split("\n").map((line, index) => {
          const source = byLine.get(index);
          return <div className={`summary-line${source && !source.supported ? " unsupported" : ""}`} key={index}>
            <span className="summary-line-text">{line ? <TimestampText text={line} duration={duration} onSeek={onSeek} /> : "\u00a0"}</span>
            {source && <span className="line-source">
              {source.supported
                ? <button type="button" className="source-chip mono" onClick={() => onSeek?.(source.start_seconds)} disabled={!onSeek} title={`Passage qui traite de ce point (à relire pour les détails) : « ${source.excerpt} »`}>▸ {formatDuration(source.start_seconds)}</button>
                : <button type="button" className="source-chip unchecked" onClick={() => onSeek?.(source.start_seconds)} disabled={!onSeek} title={`Aucun passage de la vidéo ne dit clairement cela. Le plus proche (${formatDuration(source.start_seconds)}) : « ${source.excerpt} »`}>à vérifier</button>}
              {source.cited_matches === false && <span className="field-hint" title="L'horodatage écrit dans le résumé ne correspond pas au passage trouvé">horodatage à vérifier</span>}
            </span>}
          </div>;
        })}</div>
        : <div className="content-panel summary-content"><TimestampText text={summary.content_markdown} duration={duration} onSeek={onSeek} />{sources?.status === "indexing" && <p className="field-hint">Les sources de chaque ligne s&apos;afficheront à la fin de l&apos;indexation de la vidéo.</p>}</div>}
    {regenerating && <RegenerateDialog videoId={videoId} duration={duration} current={summary} onClose={() => setRegenerating(false)} onStarted={jobId => { setRegenerating(false); onJobStarted(jobId); }} />}
  </section>;
}

function RegenerateDialog({ videoId, duration, current, onClose, onStarted }: { videoId: string; duration: number; current: Summary; onClose: () => void; onStarted: (jobId: string) => void }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState(current.template_id || "");
  const [length, setLength] = useState<SummaryLength>((current.summary_length as SummaryLength) || "standard");
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { api<Template[]>("/templates").then(setTemplates).catch(reason => setError(String(reason))); }, []);

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const job = await api<{ id: string }>(`/videos/${videoId}/summaries`, json("POST", { template_id: templateId || null, summary_length: length, custom_prompt: instructions.trim() || null }));
      onStarted(job.id);
    } catch (reason) { setError(String(reason)); setBusy(false); }
  }

  return <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="regenerate-title"><form className="modal regenerate-modal" onSubmit={submit}>
    <h2 id="regenerate-title">Régénérer le résumé</h2>
    <p>La transcription est réutilisée : seul le résumé est refait. La version actuelle reste consultable.</p>
    <div className="field"><label htmlFor="regen-template">Template</label><div className="field-control"><select id="regen-template" value={templateId} onChange={event => setTemplateId(event.target.value)}><option value="">Par défaut</option>{templates.map(template => <option key={template.id} value={template.id}>{template.name}</option>)}</select><Icon name="chevron" size={14}/></div></div>
    <div className="field"><span className="field-label" id="regen-length">Longueur</span><div className="segmented" role="radiogroup" aria-labelledby="regen-length">{summaryLengths.map(option => <button type="button" role="radio" aria-checked={length === option.value} className={length === option.value ? "selected" : ""} key={option.value} title={option.hint} onClick={() => setLength(option.value)}>{option.label}</button>)}</div><span className="field-hint">~{wordBudget(duration, length)} mots pour cette vidéo</span></div>
    <div className="field"><label htmlFor="regen-instructions">Instructions supplémentaires <span className="muted">(facultatif)</span></label><textarea id="regen-instructions" rows={3} value={instructions} maxLength={CUSTOM_PROMPT_MAX_CHARS} onChange={event => setInstructions(event.target.value)} placeholder="Ex. : insiste sur les risques et les échéances…" /></div>
    {error && <div className="error" role="alert">{error}</div>}
    <div className="modal-actions"><button type="button" className="btn" onClick={onClose} disabled={busy}>Annuler</button><button className="btn primary" disabled={busy}>{busy ? "Lancement…" : "Régénérer"}</button></div>
  </form></div>;
}
