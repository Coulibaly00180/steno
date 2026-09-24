"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import { api, formatBytes, formatDuration, uploadForm } from "../lib/api";
import { Icon } from "../components/Icons";
import LinkImport, { type LinkItem } from "../components/LinkImport";
import VideoTable from "../components/VideoTable";
import { useSystemStatus } from "../lib/status";
import { useVideoList } from "../lib/library";
import { CUSTOM_PROMPT_MAX_CHARS, VOCABULARY_MAX_CHARS, safeStorage, sourceLanguages, sourcePolicies, splitTerms, summaryLengths, targetLanguages, wordBudget, type SourcePolicy, type SummaryLength } from "../lib/analysis";

const SOURCE_LANGUAGE_KEY = "video-ai-source-language";
// One POST per file; beyond this, a folder import would be the right tool.
const MAX_BATCH_FILES = 50;

type Template = { id: string; name: string; description?: string; is_default: boolean };
type Job = { id: string; video_id: string };
// While "uploading": bytes sent so far, and when the file started (for the speed).
type UploadState = { state: "waiting" | "uploading" | "done" | "error"; message?: string; videoId?: string; loaded?: number; total?: number; startedAt?: number };
type PendingFile = { key: string; file: File; upload: UploadState };

function sizeLabel(bytes: number) {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} Go`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} Mo`;
  return `${Math.round(bytes / 1024)} Ko`;
}

const fileKey = (file: File) => `${file.name}|${file.size}|${file.lastModified}`;
const isAudio = (file: File) => file.type.startsWith("audio/") || /\.(mp3|m4a|wav|flac|ogg|oga|opus)$/i.test(file.name);

/** Non-blocking warning: an upload stays possible, but would wait or fail. */
function StatusBanner({ status }: { status: ReturnType<typeof useSystemStatus>["status"] }) {
  if (!status) return null;
  const { worker, ollama, model } = status.services;
  const messages: string[] = [];
  if (worker.status !== "ok") messages.push("Aucun worker actif : les analyses resteront en attente.");
  if (ollama.status !== "ok") messages.push("Ollama est injoignable : traduction, résumés et chat échoueront.");
  else if (model.status !== "ok") messages.push(`Modèle LLM : ${model.detail || "indisponible"}.`);
  if (!messages.length) return null;
  return <div className="status-banner" role="status">{messages.map(message => <p key={message}>{message}</p>)}</div>;
}

function uploadLabel(upload: UploadState) {
  if (upload.state === "uploading") return upload.total && (upload.loaded ?? 0) >= upload.total ? "Vérification du fichier…" : "Envoi…";
  if (upload.state === "done") return "Ajouté à la file";
  if (upload.state === "error") return upload.message || "Échec de l'envoi";
  return null;
}

/** Files done and bytes sent across the whole batch. */
function BatchBar({ files }: { files: PendingFile[] }) {
  const total = files.reduce((sum, item) => sum + item.file.size, 0);
  const sent = files.reduce((sum, item) => sum + (item.upload.state === "done" ? item.file.size : item.upload.state === "uploading" ? item.upload.loaded ?? 0 : 0), 0);
  const done = files.filter(item => item.upload.state === "done").length;
  const percent = total ? Math.min(100, Math.floor((100 * sent) / total)) : 0;
  return <div className="upload-progress batch">
    <div className="progress-track small" role="progressbar" aria-label="Progression du lot" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><div style={{ width: `${percent}%` }} /></div>
    <span className="mono upload-figures">Lot : {done} / {files.length} fichiers · {formatBytes(sent)} / {formatBytes(total)} · {percent} %</span>
  </div>;
}

/** Bytes sent, percentage and speed of the file being uploaded. */
function UploadBar({ upload }: { upload: UploadState }) {
  const loaded = upload.loaded ?? 0, total = upload.total ?? 0;
  const percent = total ? Math.min(100, Math.floor((100 * loaded) / total)) : 0;
  const seconds = upload.startedAt ? (performance.now() - upload.startedAt) / 1000 : 0;
  const speed = seconds > 1 ? loaded / seconds : 0;
  return <div className="upload-progress">
    <div className="progress-track small" role="progressbar" aria-label="Progression de l'envoi" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><div style={{ width: `${percent}%` }} /></div>
    <span className="mono upload-figures">{formatBytes(loaded)} / {formatBytes(total)} · {percent} %{speed ? ` · ${formatBytes(speed)}/s` : ""}</span>
  </div>;
}

export default function Home() {
  const router = useRouter();
  const fileInput = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<PendingFile[]>([]);
  const [fileDuration, setFileDuration] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);
  const [targetLanguage, setTargetLanguage] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [customPrompt, setCustomPrompt] = useState("");
  const [summaryLength, setSummaryLength] = useState<SummaryLength>("standard");
  const [sourceLanguage, setSourceLanguage] = useState("");
  const [vocabulary, setVocabulary] = useState("");
  const [useGlossary, setUseGlossary] = useState(true);
  const [diarize, setDiarize] = useState(false);
  const [numSpeakers, setNumSpeakers] = useState("");
  const [sourcePolicy, setSourcePolicy] = useState<SourcePolicy>("keep");
  // n°12: import from a link instead of a file.
  const [mode, setMode] = useState<"file" | "link">("file");
  const [linkItems, setLinkItems] = useState<LinkItem[]>([]);
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [glossaryCount, setGlossaryCount] = useState<number | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [batchResult, setBatchResult] = useState("");
  const { status } = useSystemStatus();
  const recent = useVideoList("limit=8");
  const uploadAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    api<Template[]>("/templates").then(rows => {
      setTemplates(rows);
      const defaultTemplate = rows.find(item => item.is_default);
      if (defaultTemplate) setTemplateId(current => current || defaultTemplate.id);
    }).catch(reason => setError(String(reason)));
  }, []);

  useEffect(() => {
    // Only the spoken language is remembered; recurring terms belong in the global glossary (F-11.6).
    const saved = safeStorage()?.getItem(SOURCE_LANGUAGE_KEY) ?? "";
    if (sourceLanguages.some(language => language.code === saved)) { setSourceLanguage(saved); setAdvancedOpen(true); }
    api<{ terms: string[] }>("/glossary").then(glossary => setGlossaryCount(glossary.terms.length)).catch(() => setGlossaryCount(null));
  }, []);

  function chooseSourceLanguage(value: string) {
    setSourceLanguage(value);
    try { if (value) safeStorage()?.setItem(SOURCE_LANGUAGE_KEY, value); else safeStorage()?.removeItem(SOURCE_LANGUAGE_KEY); } catch { /* storage is a convenience only */ }
  }

  // The duration (for the word estimate) is read only when a single file is chosen.
  const single = files.length === 1 ? files[0].file : null;
  useEffect(() => {
    setFileDuration(null);
    if (!single) return;
    const element = document.createElement(isAudio(single) ? "audio" : "video");
    const url = URL.createObjectURL(single);
    element.preload = "metadata"; element.src = url;
    element.onloadedmetadata = () => { if (Number.isFinite(element.duration)) setFileDuration(element.duration); URL.revokeObjectURL(url); };
    element.onerror = () => URL.revokeObjectURL(url);
    return () => URL.revokeObjectURL(url);
  }, [single]);

  function addFiles(list: FileList | null) {
    // Copied now: the FileList is live, and resetting the input below empties it
    // before React runs the state updater.
    const chosen = Array.from(list ?? []);
    if (!chosen.length) return;
    setBatchResult(""); setError("");
    setFiles(current => {
      // Files already sent leave the list once new ones are added.
      const kept = current.filter(item => item.upload.state !== "done");
      const known = new Set(kept.map(item => item.key));
      const added = chosen.filter(file => !known.has(fileKey(file))).map(file => ({ key: fileKey(file), file, upload: { state: "waiting" as const } }));
      const next = [...kept, ...added];
      if (next.length > MAX_BATCH_FILES) setError(`${MAX_BATCH_FILES} fichiers au plus par import : les suivants sont ignorés.`);
      return next.slice(0, MAX_BATCH_FILES);
    });
    if (fileInput.current) fileInput.current.value = "";
  }

  function removeFile(key: string) { setFiles(current => current.filter(item => item.key !== key)); }

  function drop(event: DragEvent<HTMLElement>) {
    event.preventDefault(); setDragging(false);
    addFiles(event.dataTransfer.files);
  }

  function setUpload(key: string, upload: UploadState) {
    setFiles(current => current.map(item => item.key === key ? { ...item, upload } : item));
  }

  async function uploadOne(item: PendingFile): Promise<Job | null> {
    const form = new FormData(); form.append("file", item.file);
    if (targetLanguage) form.append("target_language", targetLanguage);
    if (templateId) form.append("template_id", templateId);
    if (customPrompt.trim()) form.append("custom_prompt", customPrompt.trim());
    form.append("summary_length", summaryLength);
    if (sourceLanguage) form.append("source_language", sourceLanguage);
    if (vocabulary.trim()) form.append("vocabulary", vocabulary.trim());
    form.append("use_global_glossary", useGlossary && glossaryCount !== 0 ? "true" : "false");
    if (diarize) { form.append("diarize", "true"); if (numSpeakers) form.append("num_speakers", numSpeakers); }
    if (sourcePolicy !== "keep") form.append("source_policy", sourcePolicy);
    const startedAt = performance.now();
    setUpload(item.key, { state: "uploading", loaded: 0, total: item.file.size, startedAt });
    const controller = new AbortController();
    uploadAbort.current = controller;
    try {
      const job = await uploadForm<Job>("/videos", form, (loaded, total) => setUpload(item.key, { state: "uploading", loaded, total, startedAt }), controller.signal);
      setUpload(item.key, { state: "done", videoId: job.video_id });
      return job;
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") setUpload(item.key, { state: "error", message: "Envoi annulé" });
      else setUpload(item.key, { state: "error", message: reason instanceof Error ? reason.message : String(reason) });
      return null;
    } finally { uploadAbort.current = null; }
  }

  /** The form's options as JSON, for the link imports. */
  function jsonOptions() {
    return {
      target_language: targetLanguage || null, template_id: templateId || null, custom_prompt: customPrompt.trim() || null,
      summary_length: summaryLength, source_language: sourceLanguage || null, vocabulary: vocabulary.trim() || null,
      use_global_glossary: useGlossary && glossaryCount !== 0, diarize, num_speakers: diarize && numSpeakers ? Number(numSpeakers) : null,
      source_policy: sourcePolicy,
    };
  }

  async function importLinks() {
    const chosen = linkItems.filter(item => item.selected);
    if (!chosen.length || !rightsConfirmed) return;
    setBusy(true); setError(""); setBatchResult("");
    const jobs: Job[] = [];
    const failures: string[] = [];
    for (const item of chosen) {
      try { jobs.push(await api<Job>("/imports/url", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: item.url, title: item.title, ...jsonOptions() }) })); }
      catch (reason) { failures.push(`${item.title} : ${reason instanceof Error ? reason.message : String(reason)}`); }
    }
    setBusy(false);
    if (chosen.length === 1 && jobs.length === 1) { router.push(`/videos/${jobs[0].video_id}?job=${jobs[0].id}`); return; }
    if (failures.length) setError(failures.join(" · "));
    setBatchResult(`${jobs.length} import${jobs.length > 1 ? "s" : ""} ajouté${jobs.length > 1 ? "s" : ""} à la file : chaque fichier sera téléchargé puis analysé.`);
    setLinkItems([]); setRightsConfirmed(false);
    void recent.reload();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (mode === "link") { await importLinks(); return; }
    const pending = files.filter(item => item.upload.state !== "done");
    if (!pending.length) return;
    setBusy(true); setError(""); setBatchResult("");
    // One file after the other: the uploads share the disk and the network, and
    // the queue keeps the order in which the files were chosen.
    const jobs: Job[] = [];
    for (const item of pending) {
      const job = await uploadOne(item);
      if (job) { jobs.push(job); void recent.reload(); }
    }
    setBusy(false);
    if (pending.length === 1 && jobs.length === 1) { router.push(`/videos/${jobs[0].video_id}?job=${jobs[0].id}`); return; }
    const failed = pending.length - jobs.length;
    setBatchResult(`${jobs.length} analyse${jobs.length > 1 ? "s" : ""} ajoutée${jobs.length > 1 ? "s" : ""} à la file${failed ? ` · ${failed} en échec (voir ci-dessus)` : ""}.`);
    void recent.reload();
  }

  const toSend = mode === "link" ? (rightsConfirmed ? linkItems.filter(item => item.selected).length : 0) : files.filter(item => item.upload.state !== "done").length;

  return <div className="page">
    <header className="page-header"><h1>Analyser une vidéo</h1><p>Transcrivez, traduisez et résumez vos vidéos avec votre IA locale.</p></header>
    <StatusBanner status={status} />
    <div className="segmented import-mode" role="radiogroup" aria-label="Source de l'import"><button type="button" role="radio" aria-checked={mode === "file"} className={mode === "file" ? "selected" : ""} onClick={() => setMode("file")} disabled={busy}><Icon name="upload" size={14}/> Fichier</button><button type="button" role="radio" aria-checked={mode === "link"} className={mode === "link" ? "selected" : ""} onClick={() => setMode("link")} disabled={busy}><Icon name="link" size={14}/> Lien</button><Link className="text-link record-link" href="/record"><Icon name="mic" size={14}/> ou enregistrer depuis le navigateur</Link></div>
    <form onSubmit={submit}>
      <input ref={fileInput} hidden type="file" multiple accept="video/*,audio/*,.mkv,.flac,.ogv,.ogg,.oga,.opus" onChange={event => addFiles(event.target.files)} />
      {mode === "link" ? <LinkImport items={linkItems} onItems={setLinkItems} confirmed={rightsConfirmed} onConfirmed={setRightsConfirmed} disabled={busy} /> : !files.length ? <button type="button" className={`dropzone${dragging ? " dragging" : ""}`} onClick={() => fileInput.current?.click()} onDragEnter={event => { event.preventDefault(); setDragging(true); }} onDragOver={event => event.preventDefault()} onDragLeave={() => setDragging(false)} onDrop={drop}>
        <span className="drop-icon"><Icon name="upload" size={22}/></span><span className="drop-title">Déposez une ou plusieurs vidéos ou fichiers audio</span><span className="drop-subtitle">ou cliquez pour parcourir</span><span className="drop-formats">MP4 · MOV · MKV · WEBM · OGV · MP3 · WAV · FLAC · OGG</span><span className="drop-limit">Jusqu&apos;à 6 h · 2 Go max par fichier · {MAX_BATCH_FILES} fichiers max</span>
      </button> : <div className={`file-list${dragging ? " dragging" : ""}`} onDragEnter={event => { event.preventDefault(); setDragging(true); }} onDragOver={event => event.preventDefault()} onDragLeave={() => setDragging(false)} onDrop={drop}>
        {files.map(item => { const label = uploadLabel(item.upload); return <div className={`file-card upload-${item.upload.state}`} key={item.key}>
          <span className="file-icon"><Icon name={isAudio(item.file) ? "audio" : "video"} size={19}/></span>
          <div className="file-info"><div className="file-name">{item.upload.state === "done" && item.upload.videoId ? <Link href={`/videos/${item.upload.videoId}`}>{item.file.name}</Link> : item.file.name}</div><div className="file-meta">{sizeLabel(item.file.size)}{single && fileDuration !== null ? ` · ${formatDuration(fileDuration).replace(/^00:/, "")}` : ""}{label && <span className={`upload-state ${item.upload.state}`}> · {label}</span>}</div>{item.upload.state === "uploading" && <UploadBar upload={item.upload} />}</div>
          {item.upload.state === "uploading" && <button type="button" className="btn small" onClick={() => uploadAbort.current?.abort()}>Annuler l&apos;envoi</button>}
          {item.upload.state !== "uploading" && item.upload.state !== "done" && <button type="button" className="icon-btn" aria-label={`Retirer ${item.file.name}`} onClick={() => removeFile(item.key)} disabled={busy}><Icon name="close" size={14}/></button>}
        </div>; })}
        {busy && files.length > 1 && <BatchBar files={files} />}
        <div className="file-list-actions"><span className="field-hint">{files.length} fichier{files.length > 1 ? "s" : ""} · les mêmes réglages s&apos;appliquent à tous</span><button type="button" className="btn" onClick={() => fileInput.current?.click()} disabled={busy || files.length >= MAX_BATCH_FILES}>Ajouter des fichiers</button></div>
      </div>}

      <div className="card options-card">
        <div className="form-grid">
          <div className="field"><label htmlFor="language">Langue de traduction</label><div className="field-control"><select id="language" value={targetLanguage} onChange={event => setTargetLanguage(event.target.value)}><option value="">Pas de traduction</option>{targetLanguages.map(language => <option value={language.value} key={language.value}>{language.label}</option>)}</select><Icon name="chevron" size={14}/></div></div>
          <div className="field"><label htmlFor="template">Template de résumé</label><div className="field-control"><select id="template" value={templateId} onChange={event => setTemplateId(event.target.value)}><option value="">Par défaut</option>{templates.map(template => <option value={template.id} key={template.id}>{template.name}</option>)}</select><Icon name="chevron" size={14}/></div><Link className="text-link" href="/templates">Gérer les templates</Link></div>
        </div>
        <div className="field"><span className="field-label" id="length-label">Longueur du résumé</span><div className="segmented" role="radiogroup" aria-labelledby="length-label">{summaryLengths.map(option => <button type="button" role="radio" aria-checked={summaryLength === option.value} className={summaryLength === option.value ? "selected" : ""} title={option.hint} key={option.value} onClick={() => setSummaryLength(option.value)}>{option.label}</button>)}</div><span className="field-hint">{single && fileDuration !== null ? `~${wordBudget(fileDuration, summaryLength)} mots pour cette vidéo` : files.length > 1 ? "Longueur adaptée à la durée de chaque fichier." : summaryLengths.find(option => option.value === summaryLength)?.hint}</span></div>
        <div className="field speakers-option"><label className="checkbox"><input type="checkbox" checked={diarize} onChange={event => setDiarize(event.target.checked)} /><span>Identifier les intervenants <span className="muted">(réunion, interview : qui parle, et quand)</span></span></label>{diarize && <label className="speaker-count">Nombre d&apos;intervenants <input type="number" min={1} max={20} value={numSpeakers} onChange={event => setNumSpeakers(event.target.value)} placeholder="auto" /><span className="field-hint">Facultatif, mais plus fiable s&apos;il est connu.</span></label>}</div>
        <details className="advanced" open={advancedOpen} onToggle={event => setAdvancedOpen(event.currentTarget.open)}>
          <summary>Options avancées</summary>
          <div className="form-grid">
            <div className="field"><label htmlFor="source-language">Langue parlée</label><div className="field-control"><select id="source-language" value={sourceLanguage} onChange={event => chooseSourceLanguage(event.target.value)}><option value="">Détection automatique</option>{sourceLanguages.map(language => <option value={language.code} key={language.code}>{language.label}</option>)}</select><Icon name="chevron" size={14}/></div><span className="field-hint">À forcer si la détection se trompe (accent, introduction musicale).</span></div>
            <div className="field"><span className="field-label">Glossaire global</span>{glossaryCount === 0 ? <span className="field-hint">Aucun glossaire global · <Link className="text-link" href="/settings">Créer</Link></span> : <label className="checkbox"><input type="checkbox" checked={useGlossary} onChange={event => setUseGlossary(event.target.checked)} /><span>Utiliser le glossaire global{glossaryCount !== null ? ` (${glossaryCount} terme${glossaryCount > 1 ? "s" : ""})` : ""} · <Link className="text-link" href="/settings">Modifier</Link></span></label>}</div>
          </div>
          <div className="field"><label htmlFor="vocabulary">Vocabulaire {files.length > 1 ? "de ces vidéos" : "de cette vidéo"} <span className="muted">(facultatif)</span></label><textarea id="vocabulary" rows={2} value={vocabulary} maxLength={VOCABULARY_MAX_CHARS} onChange={event => setVocabulary(event.target.value)} placeholder="Noms propres, sigles, jargon : Doñana, OKR, Kubernetes…" /><span className="field-hint">{splitTerms(vocabulary).length} terme(s) · {vocabulary.length}/{VOCABULARY_MAX_CHARS} caractères · séparés par des virgules ou des retours à la ligne</span></div>
          <div className="field"><label htmlFor="source-policy">Après l&apos;analyse</label><div className="field-control"><select id="source-policy" value={sourcePolicy} onChange={event => setSourcePolicy(event.target.value as SourcePolicy)}>{sourcePolicies.map(option => <option value={option.value} key={option.value}>{option.label}</option>)}</select><Icon name="chevron" size={14}/></div><span className="field-hint">{sourcePolicies.find(option => option.value === sourcePolicy)?.hint} Réglable plus tard dans Paramètres › Espace disque.</span></div>
          <div className="field"><label htmlFor="instructions">Instructions supplémentaires <span className="muted">(facultatif, ajoutées au template)</span></label><textarea id="instructions" rows={2} value={customPrompt} maxLength={CUSTOM_PROMPT_MAX_CHARS} onChange={event => setCustomPrompt(event.target.value)} placeholder="Ex. : concentre le résumé sur les décisions prises et les prochaines actions…" /></div>
        </details>
        {error && <div className="error" role="alert">{error}</div>}
        {batchResult && <div className="status-banner success-banner" role="status"><p>{batchResult} <Link className="text-link" href="/library">Voir la bibliothèque</Link></p></div>}
        <div className="form-actions"><span className="form-note">Le traitement est effectué localement.</span><button className="btn primary" type="submit" disabled={!toSend || busy}>{busy ? "Import en cours…" : toSend > 1 ? `Lancer les ${toSend} analyses` : "Lancer l'analyse"}<Icon name="arrow" size={14}/></button></div>
      </div>
    </form>

    <section style={{ marginTop: 40 }}><div className="section-heading"><h2 className="section-title">Analyses récentes</h2><button className="btn" onClick={() => void recent.reload()}>Actualiser</button></div>{recent.error && <div className="error">{recent.error}</div>}{recent.videos && <VideoTable videos={recent.videos} onChanged={recent.reload}/>}</section>
  </div>;
}
