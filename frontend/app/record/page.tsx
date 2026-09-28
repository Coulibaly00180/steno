"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { API, api, formatBytes, formatDuration } from "../../lib/api";
import { Icon } from "../../components/Icons";
import { sourceLanguages, sourcePolicies, summaryLengths, targetLanguages, type SourcePolicy, type SummaryLength } from "../../lib/analysis";
import { CHUNK_MS, ChunkUploader, openCapture, pickMimeType, readLevel, recordingSupported, tabCaptureSupported, type Capture, type RecordingSource } from "../../lib/recorder";
import { useSystemStatus } from "../../lib/status";

type Recording = { id: string; title: string; status: string; live: boolean; sides?: boolean; size_bytes: number; live_error: string | null; created_at: string; updated_at: string };
type Template = { id: string; name: string; is_default: boolean };
type LiveLine = { start: number; end: number; text: string; side?: "you" | "others" | null };
const SIDE_LABELS = { you: "Vous", others: "Participants" } as const;
type Phase = "idle" | "starting" | "recording" | "paused" | "finishing";

const sources: { value: RecordingSource; label: string; hint: string }[] = [
  { value: "mic", label: "Micro", hint: "Une réunion en salle, une dictée, un entretien." },
  { value: "tab", label: "Son d'un onglet ou d'une fenêtre", hint: "Une visioconférence ouverte dans le navigateur : choisissez son onglet et cochez « Partager l'audio »." },
  { value: "both", label: "Les deux", hint: "Une visioconférence : vous (micro) et vos interlocuteurs (onglet), gardés sur deux pistes. La transcription sait qui parle de quel côté, et l'écho de vos haut-parleurs est retiré. Le son d'un onglet se partage dans Chrome et Edge ; pour une application de bureau (Teams, Zoom), partagez l'écran entier avec le son du système (Windows)." },
];

// A side never louder than this after SILENT_SIDE_SECONDS is reported as not captured.
const SILENT_SIDE_SECONDS = 20;
const HEARD_LEVEL = 0.03;

function defaultTitle() {
  return `Enregistrement du ${new Date().toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }).replace(" ", " à ")}`;
}

export default function RecordPage() {
  const router = useRouter();
  const { status } = useSystemStatus();
  // Unknown until the first status check: only a service known to be stopped disables the live transcript.
  const liveAvailable = status?.services.live?.status !== "down";
  const [supported, setSupported] = useState(true);
  const [source, setSource] = useState<RecordingSource>("mic");
  const [title, setTitle] = useState("");
  const [liveWanted, setLiveWanted] = useState(true);
  const [language, setLanguage] = useState("");
  const [targetLanguage, setTargetLanguage] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [summaryLength, setSummaryLength] = useState<SummaryLength>("standard");
  const [diarize, setDiarize] = useState(false);
  const [sourcePolicy, setSourcePolicy] = useState<SourcePolicy>("keep");
  const [templates, setTemplates] = useState<Template[]>([]);
  const [unfinished, setUnfinished] = useState<Recording[]>([]);
  const [phase, setPhase] = useState<Phase>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const [sideLevels, setSideLevels] = useState<{ you: number; others: number } | null>(null);
  // Loudest level heard on each side so far: a side still silent after a while is probably not captured.
  const heardRef = useRef({ you: 0, others: 0 });
  const [, setUploadTick] = useState(0);
  const [lines, setLines] = useState<LiveLine[]>([]);
  const [liveNote, setLiveNote] = useState("");
  const [error, setError] = useState("");
  const recordingRef = useRef<Recording | null>(null);
  const captureRef = useRef<Capture | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const uploaderRef = useRef<ChunkUploader | null>(null);
  const liveRef = useRef<EventSource | null>(null);
  const clockRef = useRef<{ startedAt: number; before: number }>({ startedAt: 0, before: 0 });
  const linesEnd = useRef<HTMLDivElement>(null);

  const loadUnfinished = useCallback(async () => {
    try { setUnfinished(await api<Recording[]>("/recordings")); } catch { /* shown again on the next visit */ }
  }, []);

  useEffect(() => {
    setSupported(recordingSupported());
    setTitle(defaultTitle());
    api<Template[]>("/templates").then(rows => { setTemplates(rows); const fallback = rows.find(row => row.is_default); if (fallback) setTemplateId(fallback.id); }).catch(() => setTemplates([]));
    void loadUnfinished();
  }, [loadUnfinished]);

  const active = phase === "recording" || phase === "paused" || phase === "finishing";
  // Leaving the page would stop the recording: ask first.
  useEffect(() => {
    if (!active) return;
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [active]);

  // Timer and level meter.
  useEffect(() => {
    if (phase !== "recording") return;
    const buffer = new Uint8Array(new ArrayBuffer(1024));
    let frame = 0;
    const tick = () => {
      const clock = clockRef.current;
      setElapsed(clock.before + (performance.now() - clock.startedAt) / 1000);
      const capture = captureRef.current;
      if (capture) {
        setLevel(readLevel(capture.level, buffer));
        if (capture.sideLevels) {
          const you = readLevel(capture.sideLevels.you, buffer), others = readLevel(capture.sideLevels.others, buffer);
          heardRef.current = { you: Math.max(heardRef.current.you, you), others: Math.max(heardRef.current.others, others) };
          setSideLevels({ you, others });
        }
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [phase]);

  useEffect(() => { linesEnd.current?.scrollIntoView({ block: "nearest" }); }, [lines]);
  useEffect(() => () => { liveRef.current?.close(); captureRef.current?.stop(); }, []);

  function followLive(recordingId: string) {
    const events = new EventSource(`${API}/recordings/${recordingId}/live`);
    events.addEventListener("segment", event => {
      try { const line = JSON.parse((event as MessageEvent).data) as LiveLine; setLines(current => [...current, line]); } catch { /* ignored */ }
    });
    events.addEventListener("state", event => {
      try { const state = JSON.parse((event as MessageEvent).data) as { error?: string }; if (state.error) setLiveNote(state.error); } catch { /* ignored */ }
    });
    events.addEventListener("end", () => events.close());
    liveRef.current = events;
  }

  async function start() {
    const mimeType = pickMimeType();
    if (!mimeType) { setError("Ce navigateur ne sait pas enregistrer l'audio dans un format pris en charge."); return; }
    setError(""); setLines([]); setLiveNote(""); setPhase("starting");
    let capture: Capture | null = null;
    try {
      capture = await openCapture(source);
      const live = liveWanted && liveAvailable;
      const recording = await api<Recording>("/recordings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: title.trim() || defaultTitle(), mime_type: mimeType, live, language: language || null, sides: capture.sides }) });
      const uploader = new ChunkUploader(recording.id, () => setUploadTick(value => value + 1));
      const recorder = new MediaRecorder(capture.stream, { mimeType, audioBitsPerSecond: 64000 });
      recorder.ondataavailable = event => uploader.push(event.data);
      // The shared tab was closed, or the microphone unplugged: stop cleanly.
      capture.inputs.forEach(track => { track.onended = () => { if (recorder.state !== "inactive") void stop(); }; });
      recorder.start(CHUNK_MS);
      captureRef.current = capture; recorderRef.current = recorder; uploaderRef.current = uploader; recordingRef.current = recording;
      clockRef.current = { startedAt: performance.now(), before: 0 };
      heardRef.current = { you: 0, others: 0 }; setSideLevels(null);
      setElapsed(0); setPhase("recording");
      if (live) followLive(recording.id);
    } catch (reason) {
      capture?.stop();
      setError(reason instanceof Error ? reason.message : String(reason));
      setPhase("idle");
    }
  }

  function togglePause() {
    const recorder = recorderRef.current;
    if (!recorder) return;
    if (recorder.state === "recording") {
      recorder.pause();
      clockRef.current.before += (performance.now() - clockRef.current.startedAt) / 1000;
      setPhase("paused");
    } else if (recorder.state === "paused") {
      recorder.resume();
      clockRef.current.startedAt = performance.now();
      setPhase("recording");
    }
  }

  function options() {
    return {
      target_language: targetLanguage || null, template_id: templateId || null, summary_length: summaryLength,
      source_language: language || null, use_global_glossary: true, diarize, source_policy: sourcePolicy,
    };
  }

  async function stop() {
    const recorder = recorderRef.current, uploader = uploaderRef.current, recording = recordingRef.current;
    if (!recorder || !uploader || !recording || recorder.state === "inactive") return;
    setPhase("finishing");
    const stopped = new Promise<void>(resolve => recorder.addEventListener("stop", () => resolve(), { once: true }));
    recorder.stop();
    await stopped;
    captureRef.current?.stop();
    liveRef.current?.close();
    try {
      await uploader.flush();
      const job = await api<{ id: string; video_id: string }>(`/recordings/${recording.id}/finish`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(options()) });
      router.push(`/videos/${job.video_id}?job=${job.id}`);
    } catch (reason) {
      // Everything sent is kept on the server: it can be analysed from the list below.
      setError(`${reason instanceof Error ? reason.message : String(reason)} L'enregistrement est conservé : vous pouvez réessayer ci-dessous.`);
      setPhase("idle");
      void loadUnfinished();
    }
  }

  async function finishUnfinished(recording: Recording) {
    setError("");
    try {
      const job = await api<{ id: string; video_id: string }>(`/recordings/${recording.id}/finish`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(options()) });
      router.push(`/videos/${job.video_id}?job=${job.id}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  async function discard(recording: Recording) {
    if (!window.confirm(`Abandonner définitivement « ${recording.title} » ?`)) return;
    try { await api(`/recordings/${recording.id}`, { method: "DELETE" }); await loadUnfinished(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }

  const uploader = uploaderRef.current;
  const leftovers = unfinished.filter(item => item.id !== recordingRef.current?.id || !active);

  return <div className="page">
    <header className="page-header"><h1>Enregistrer</h1><p>Enregistrez une réunion depuis le navigateur : le compte-rendu est préparé dès l&apos;arrêt.</p></header>
    {!supported && <div className="error">Ce navigateur ne permet pas d&apos;enregistrer. Utilisez une version récente de Chrome, Edge ou Firefox, sur http://127.0.0.1:3000 ou http://localhost:3000.</div>}
    {!!leftovers.length && <div className="status-banner" role="status">{leftovers.map(item => <div className="unfinished" key={item.id}><p>Enregistrement interrompu : <strong>{item.title}</strong> ({formatBytes(item.size_bytes)}, {new Date(item.updated_at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })})</p><span className="row"><button type="button" className="btn small primary" onClick={() => void finishUnfinished(item)} disabled={active}>Analyser ce qui a été enregistré</button><button type="button" className="btn small danger" onClick={() => void discard(item)} disabled={active}>Abandonner</button></span></div>)}</div>}

    {!active && <section className="card record-setup">
      <div className="field"><label htmlFor="record-title">Titre</label><input id="record-title" value={title} maxLength={200} onChange={event => setTitle(event.target.value)} /></div>
      <div className="field"><span className="field-label" id="record-source">Source</span><div className="segmented" role="radiogroup" aria-labelledby="record-source">{sources.map(option => <button type="button" role="radio" aria-checked={source === option.value} className={source === option.value ? "selected" : ""} key={option.value} disabled={option.value !== "mic" && !tabCaptureSupported()} onClick={() => setSource(option.value)}>{option.label}</button>)}</div><span className="field-hint">{sources.find(option => option.value === source)?.hint}</span></div>
      <div className="field"><label className="checkbox"><input type="checkbox" checked={liveWanted && liveAvailable} disabled={!liveAvailable} onChange={event => setLiveWanted(event.target.checked)} /><span>Transcription en direct {liveAvailable ? <span className="muted">(aperçu ; la transcription complète est refaite à la fin)</span> : <span className="muted">(service indisponible : la transcription se fera à la fin)</span>}</span></label></div>
      <div className="form-grid">
        <div className="field"><label htmlFor="record-language">Langue parlée</label><select id="record-language" value={language} onChange={event => setLanguage(event.target.value)}><option value="">Détection automatique</option>{sourceLanguages.map(item => <option key={item.code} value={item.code}>{item.label}</option>)}</select></div>
        <div className="field"><label htmlFor="record-target">Langue de traduction</label><select id="record-target" value={targetLanguage} onChange={event => setTargetLanguage(event.target.value)}><option value="">Pas de traduction</option>{targetLanguages.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></div>
        <div className="field"><label htmlFor="record-template">Template de résumé</label><select id="record-template" value={templateId} onChange={event => setTemplateId(event.target.value)}><option value="">Par défaut</option>{templates.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div>
        <div className="field"><label htmlFor="record-length">Longueur du résumé</label><select id="record-length" value={summaryLength} onChange={event => setSummaryLength(event.target.value as SummaryLength)}>{summaryLengths.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></div>
        <div className="field"><label htmlFor="record-policy">Après l&apos;analyse</label><select id="record-policy" value={sourcePolicy} onChange={event => setSourcePolicy(event.target.value as SourcePolicy)}>{sourcePolicies.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></div>
        <div className="field record-diarize"><label className="checkbox"><input type="checkbox" checked={diarize} onChange={event => setDiarize(event.target.checked)} /><span>{source === "both" ? "Distinguer plusieurs voix de chaque côté" : "Identifier les intervenants"}</span></label></div>
      </div>
      <p className="field-hint">Prévenez les participants avant d&apos;enregistrer. L&apos;audio part au fil de l&apos;eau vers votre installation Sténo, jamais ailleurs : si l&apos;onglet se ferme, ce qui a été enregistré est conservé.</p>
      <div className="form-actions"><span /><button type="button" className="btn primary" onClick={() => void start()} disabled={!supported || phase === "starting"}><Icon name="mic" size={15} />{phase === "starting" ? "Préparation…" : "Démarrer l'enregistrement"}</button></div>
    </section>}

    {active && <section className="card recording-card" aria-live="polite">
      <div className="spread">
        <div className="recording-state"><span className={`rec-dot${phase === "recording" ? " on" : ""}`} /><strong className="mono recording-clock">{formatDuration(elapsed)}</strong><span className="muted">{phase === "paused" ? "En pause" : phase === "finishing" ? "Finalisation…" : "Enregistrement en cours"}</span></div>
        <div className="row"><button type="button" className="btn" onClick={togglePause} disabled={phase === "finishing"}><Icon name="pause" size={14} />{phase === "paused" ? "Reprendre" : "Pause"}</button><button type="button" className="btn primary" onClick={() => void stop()} disabled={phase === "finishing"}><Icon name="stop" size={14} />{phase === "finishing" ? "Envoi des dernières secondes…" : "Arrêter et analyser"}</button></div>
      </div>
      {sideLevels ? <div className="side-meters">
        {([["you", "Vous (micro)"], ["others", "Participants (onglet)"]] as const).map(([side, label]) => <div key={side} className="side-meter">
          <span className="field-hint">{label}</span>
          <div className="level-meter" aria-hidden="true"><div style={{ width: `${Math.round(sideLevels[side] * 100)}%` }} /></div>
        </div>)}
        {elapsed > SILENT_SIDE_SECONDS && heardRef.current.others < HEARD_LEVEL && <p className="field-hint warning-text">Aucun son de l&apos;onglet depuis le début : vérifiez que « Partager l&apos;audio » était coché, ou que la réunion a commencé.</p>}
        {elapsed > SILENT_SIDE_SECONDS && heardRef.current.you < HEARD_LEVEL && <p className="field-hint warning-text">Aucun son du micro depuis le début : vérifiez qu&apos;il n&apos;est pas coupé.</p>}
      </div> : <div className="level-meter" aria-hidden="true"><div style={{ width: `${Math.round(level * 100)}%` }} /></div>}
      <p className="field-hint">{recordingRef.current?.title} · {formatBytes(uploader?.sentBytes ?? 0)} envoyés{uploader?.pending ? ` · ${uploader.pending} morceau(x) en attente` : ""}</p>
      {uploader?.error && <div className="status-banner" role="status"><p>{uploader.error.message}</p></div>}
      {recordingRef.current?.live && <div className="live-transcript">
        <p className="field-label">Transcription en direct</p>
        {liveNote && <p className="field-hint">{liveNote}</p>}
        {!lines.length && !liveNote && <p className="muted">Les premières phrases apparaissent après quelques secondes de parole…</p>}
        {lines.map((line, index) => <p key={`${line.start}-${index}`}><span className="mono">{formatDuration(line.start)}</span>{line.side && <strong className={`live-side side-${line.side}`}>{SIDE_LABELS[line.side]} : </strong>}{line.text}</p>)}
        <div ref={linesEnd} />
      </div>}
    </section>}
    {error && <div className="error" role="alert">{error}</div>}
  </div>;
}
