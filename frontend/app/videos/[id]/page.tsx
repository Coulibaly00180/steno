"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useParams, useRouter } from "next/navigation";
import { API, api, formatDuration } from "../../../lib/api";
import { Icon } from "../../../components/Icons";
import MediaPlayer from "../../../components/MediaPlayer";
import ActionsPanel from "../../../components/ActionsPanel";
import ClipsPanel, { type ClipRange } from "../../../components/ClipsPanel";
import { SeriesBar, SeriesChanges } from "../../../components/SeriesPanel";
import AnswerFeedback from "../../../components/AnswerFeedback";
import GlossarySuggestions from "../../../components/GlossarySuggestions";
import ReplaceBar from "../../../components/ReplaceBar";
import SpeakersPanel, { speakerColor, type Speaker } from "../../../components/SpeakersPanel";
import SummaryPanel, { type Summary } from "../../../components/SummaryPanel";
import TagEditor from "../../../components/TagEditor";
import TimestampText from "../../../components/TimestampText";
import TranscriptSearch, { type TranscriptLine } from "../../../components/TranscriptSearch";
import VideoEntities from "../../../components/VideoEntities";
import { ChatError, streamChat, type ChatMessage } from "../../../lib/chat";
import { TERMINAL_STATUSES as TERMINAL, cancelJob, queueText, stageLabels as stages, type Job } from "../../../lib/jobs";
import { useJobNotifications } from "../../../lib/notifications";

type Segment = { id: number; start_seconds: number; end_seconds: number; text: string; speaker_id?: number | null; doubts?: number[][] };
type Chapter = { start_seconds: number; title: string };
type Video = {
  id: string; original_filename: string; duration_seconds: number; size_bytes: number; status: string;
  detected_language?: string; target_language?: string; transcript_text?: string; translated_text?: string;
  source_language_forced?: boolean; vocabulary?: string[]; glossary_snapshot?: string[]; whisper_terms_count?: number; llm_terms_count?: number;
  media_kind: string; source_available: boolean; audio_available: boolean; chapters: Chapter[];
  summary_outdated: boolean; translation_outdated: boolean; tags: string[]; chat_mode: "none" | "full" | "passages" | "partial";
  speakers: Speaker[]; diarization_error?: string | null; source_policy?: "keep" | "audio" | "delete"; source_url?: string | null;
  segments: Segment[]; summaries: Summary[]; job?: Job | null;
};

/** Index of the last item starting at or before `seconds` (-1 before the first). */
function indexAt(starts: number[], seconds: number) {
  let low = 0, high = starts.length - 1, found = -1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (starts[middle] <= seconds + 0.05) { found = middle; low = middle + 1; } else high = middle - 1;
  }
  return found;
}

export default function VideoPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [queryJobId, setQueryJobId] = useState<string | null>(null);
  const [video, setVideo] = useState<Video | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [tab, setTab] = useState<"summary" | "transcript" | "translation" | "chat">("summary");
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [sseAttempt, setSseAttempt] = useState(0);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [chatLoaded, setChatLoaded] = useState(false);
  // Question being answered and the answer received so far (n°16).
  const [pending, setPending] = useState<{ question: string; answer: string } | null>(null);
  const chatAbort = useRef<AbortController | null>(null);
  // What the DOCX/PDF report carries as its annex (n°20).
  const [annex, setAnnex] = useState<"original" | "translation" | "none">("original");
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  // One search per tab, kept when switching tabs (F-3.7).
  const [transcriptQuery, setTranscriptQuery] = useState("");
  const [translationQuery, setTranslationQuery] = useState("");
  const [correcting, setCorrecting] = useState(false);
  // A chapter's scissors fill the clip form (n°7); a series change refreshes the comparison (n°6).
  const [clipRange, setClipRange] = useState<ClipRange | null>(null);
  const [seriesKey, setSeriesKey] = useState(0);
  // Playback position is reduced to the current segment and chapter: re-rendering
  // a 6-hour transcript four times a second would make the page sluggish.
  const [playingSegment, setPlayingSegment] = useState(-1);
  const [playingChapter, setPlayingChapter] = useState(-1);
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const playerRef = useRef<HTMLDivElement>(null);
  const notifications = useJobNotifications(job, video?.original_filename);

  const loadVideo = useCallback(async () => {
    try { const loaded = await api<Video>(`/videos/${id}`); setVideo(loaded); setJob(loaded.job ?? null); setError(""); }
    catch (reason) { setError(String(reason)); }
  }, [id]);
  // "?t=" (a source cited by the library questions): open the video at that moment.
  const startAt = useRef<number | null>(null);
  useEffect(() => {
    const search = new URLSearchParams(window.location.search);
    setQueryJobId(search.get("job"));
    const seconds = Number(search.get("t"));
    startAt.current = search.has("t") && Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
    void loadVideo();
  }, [id, loadVideo]);

  const effectiveJobId = video?.job?.id ?? queryJobId;
  useEffect(() => {
    if (!effectiveJobId) return;
    const source = new EventSource(`${API}/jobs/${effectiveJobId}/events`);
    source.onmessage = event => {
      let data: Job; try { data = JSON.parse(event.data); } catch { return; }
      setJob(current => ({ ...current, ...data }));
      if (TERMINAL.includes(data.status)) { source.close(); void loadVideo(); }
    };
    source.onerror = () => { source.close(); void loadVideo(); window.setTimeout(() => setSseAttempt(value => value + 1), 1000); };
    return () => source.close();
  }, [effectiveJobId, sseAttempt, loadVideo]);

  // SSE gives immediate stage changes when the proxy keeps the stream open.  The
  // small polling fallback also refreshes the screen when a browser, proxy or
  // temporary network change drops that stream without surfacing an error.
  useEffect(() => {
    if (!effectiveJobId) return;
    let cancelled = false;
    const refreshJob = async () => {
      try {
        const current = await api<Job>(`/jobs/${effectiveJobId}`);
        if (cancelled) return;
        setJob(current);
        if (TERMINAL.includes(current.status)) void loadVideo();
      } catch {
        // The SSE handler remains the primary live channel. A later poll retries.
      }
    };
    void refreshJob();
    const interval = window.setInterval(() => void refreshJob(), 3000);
    return () => { cancelled = true; window.clearInterval(interval); };
  }, [effectiveJobId, loadVideo]);

  async function loadChat() {
    try { setChatMessages(await api<ChatMessage[]>(`/videos/${id}/chat/messages`)); setChatLoaded(true); setError(""); }
    catch (reason) { setError(String(reason)); }
  }
  // The answer streams in: the history is loaded once, no polling.
  useEffect(() => {
    if (tab !== "chat" || !video?.transcript_text) return;
    void loadChat();
  }, [tab, video?.transcript_text, id]);
  // Leaving the page stops the generation (nothing is saved for an unfinished answer).
  useEffect(() => () => chatAbort.current?.abort(), []);

  function notify(message: string) { setToast(message); window.setTimeout(() => setToast(""), 2600); }

  async function deleteVideo() {
    setDeleting(true); setError("");
    try { await api(`/videos/${id}`, { method: "DELETE" }); router.push("/library"); }
    catch (reason) { setError(String(reason)); setDeleting(false); setConfirmDelete(false); }
  }

  async function retryVideo() {
    setRetrying(true); setError("");
    try { const retried = await api<Job>(`/videos/${id}/retry`, { method: "POST" }); setQueryJobId(retried.id); setJob(retried); await loadVideo(); router.replace(`/videos/${id}?job=${retried.id}`); }
    catch (reason) { setError(String(reason)); setRetrying(false); }
  }

  async function askQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const value = question.trim(); if (!value || asking) return;
    setAsking(true); setError(""); setPending({ question: value, answer: "" }); setQuestion("");
    const controller = new AbortController(); chatAbort.current = controller;
    try {
      const saved = await streamChat(id, value, text => setPending(current => current && { ...current, answer: current.answer + text }), controller.signal);
      setChatMessages(current => [...current, ...saved]);
    } catch (reason) {
      if (controller.signal.aborted) return;
      if (reason instanceof ChatError && reason.saved) {
        // What was written is kept, flagged as interrupted.
        await loadChat();
        setError("La réponse a été interrompue ; ce qui a été écrit est conservé.");
      } else {
        // Nothing was saved: the question goes back into the box.
        setError(reason instanceof Error ? reason.message : String(reason)); setQuestion(value);
      }
    } finally { setPending(null); setAsking(false); }
  }

  async function cancelProcessing() {
    if (!job) return;
    setCancelling(true); setError("");
    try { const cancelled = await cancelJob(job.id); setJob(cancelled); setConfirmCancel(false); await loadVideo(); notify("Traitement annulé"); }
    catch (reason) { setError(String(reason)); setConfirmCancel(false); }
    finally { setCancelling(false); }
  }

  async function editSegment(line: TranscriptLine, text: string, speakerId: number | null) {
    const body: Record<string, unknown> = { text };
    if (speakerId !== (line.speakerId ?? null)) body.speaker_id = speakerId;
    try { await api(`/videos/${id}/segments/${line.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); await loadVideo(); notify("Ligne corrigée"); }
    catch (reason) { setError(String(reason)); throw reason; }
  }

  const segmentStarts = useMemo(() => (video?.segments || []).map(segment => segment.start_seconds), [video?.segments]);
  const chapterStarts = useMemo(() => (video?.chapters || []).map(chapter => chapter.start_seconds), [video?.chapters]);
  const onTime = useCallback((seconds: number) => {
    setPlayingSegment(indexAt(segmentStarts, seconds));
    setPlayingChapter(indexAt(chapterStarts, seconds));
  }, [segmentStarts, chapterStarts]);

  const seek = useCallback((seconds: number) => {
    const media = mediaRef.current;
    if (!media) return;
    media.currentTime = Math.max(0, seconds);
    void media.play().catch(() => { /* autoplay may be blocked: the position is still set */ });
    const box = playerRef.current?.getBoundingClientRect();
    if (box && (box.bottom < 0 || box.top > window.innerHeight)) playerRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  useEffect(() => {
    const media = mediaRef.current, seconds = startAt.current;
    if (!video || !media || seconds === null) return;
    startAt.current = null;
    const apply = () => { media.currentTime = seconds; };
    if (media.readyState >= 1) apply(); else media.addEventListener("loadedmetadata", apply, { once: true });
    playerRef.current?.scrollIntoView({ block: "start" });
  }, [video]);

  const speakersById = useMemo(() => new Map((video?.speakers || []).map(speaker => [speaker.id, speaker])), [video?.speakers]);
  const transcriptLines = useMemo<TranscriptLine[]>(() => (video?.segments || []).map(segment => {
    const speaker = segment.speaker_id != null ? speakersById.get(segment.speaker_id) : undefined;
    return { key: `s${segment.id}`, id: segment.id, start: segment.start_seconds, time: formatDuration(segment.start_seconds), text: segment.text, speakerId: segment.speaker_id ?? null, speaker: speaker?.label, color: speaker ? speakerColor(speaker.position) : undefined, doubts: segment.doubts };
  }), [video?.segments, speakersById]);
  const translationLines = useMemo<TranscriptLine[]>(() => (video?.translated_text || "").split("\n").filter(line => line.trim()).map((line, index) => ({ key: `t${index}`, text: line })), [video?.translated_text]);

  if (error && !video) return <div className="page"><div className="error">{error}</div></div>;
  if (!video) return <div className="page"><p className="muted">Chargement…</p></div>;
  const running = !!job && !TERMINAL.includes(job.status);
  // A queued job is dropped with its video; only a running one must be cancelled first.
  const canDelete = job?.status !== "RUNNING";
  const completed = video.status === "COMPLETED";
  const cancelledVideo = video.status === "CANCELLED";
  const canPlay = video.source_available || video.audio_available;
  const summaryJobFailed = job?.status === "FAILED" && job.kind === "SUMMARY";
  const compactJobFailed = job?.status === "FAILED" && job.kind === "COMPACT";
  const waiting = queueText(job);
  const statusBadge = completed ? ["status-completed", "Terminé"] : video.status === "FAILED" ? ["status-failed", "Échec"] : cancelledVideo ? ["status-cancelled", "Annulé"] : video.status === "QUEUED" ? ["status-queued", "En attente"] : ["status-running", "En cours"];

  return <div className="page wide">
    <header className="detail-header"><div><h1 className="detail-title">{video.original_filename}</h1><div className="meta"><span className="pill mono">{formatDuration(video.duration_seconds)}</span><span className={`status-badge ${statusBadge[0]}`}><i />{statusBadge[1]}</span>{video.detected_language && <span className="pill">Source · {video.detected_language.toUpperCase()} ({video.source_language_forced ? "forcée" : "détectée"})</span>}{!!video.llm_terms_count && <span className="pill" title={[video.vocabulary?.length ? `Vidéo : ${video.vocabulary.join(", ")}` : "", video.glossary_snapshot?.length ? `Glossaire : ${video.glossary_snapshot.join(", ")}` : "", `${video.whisper_terms_count} transmis à la transcription, ${video.llm_terms_count} au résumé`].filter(Boolean).join("\n")}>{video.llm_terms_count} termes de vocabulaire{(video.whisper_terms_count ?? 0) < video.llm_terms_count ? ` · ${video.whisper_terms_count} pour la transcription` : ""}</span>}{video.target_language && <span className="pill">Sortie · {video.target_language}</span>}{video.source_url && <a className="pill" href={video.source_url} target="_blank" rel="noopener noreferrer" title={video.source_url}>Source : lien d&apos;origine</a>}</div><TagEditor videoId={video.id} tags={video.tags} onSaved={tags => setVideo(current => current && { ...current, tags })} />{completed && <SeriesBar videoId={video.id} onChanged={() => setSeriesKey(value => value + 1)} />}{completed && <VideoEntities videoId={video.id} refreshKey={video.transcript_text} />}</div><div className="row">{(video.status === "FAILED" || cancelledVideo) && !running && <button className="btn primary" onClick={retryVideo} disabled={retrying}><Icon name="retry" size={14}/>{retrying ? "Relance…" : "Relancer"}</button>}{canDelete && <button className="btn danger" onClick={() => setConfirmDelete(true)}><Icon name="trash" size={14}/>Supprimer</button>}</div></header>

    {error && <div className="error" role="alert">{error}</div>}
    {job?.status === "FAILED" && (summaryJobFailed
      ? <div className="error">La régénération du résumé a échoué : {job.error || "erreur inconnue"}. Le résumé précédent est conservé.</div>
      : compactJobFailed ? <div className="error">La conversion en audio seul a échoué : {job.error || "erreur inconnue"}. Le fichier d&apos;origine est conservé.</div>
      : <div className="error">{job.error || "Le traitement a échoué."}</div>)}
    {job?.status === "CANCELLED" && <div className="status-banner" role="status"><p>{job.kind === "SUMMARY" ? "La régénération du résumé a été annulée. Le résumé précédent est conservé." : job.kind === "DIARIZE" ? "L'identification des intervenants a été annulée. La transcription est inchangée." : job.kind === "COMPACT" ? "La conversion en audio seul a été annulée. Le fichier d'origine est conservé." : "Traitement annulé. Vous pouvez le relancer ou supprimer la vidéo."}</p></div>}
    {running && <section className="card progress-card"><div className="spread"><strong style={{ fontSize: 13.5 }}>{job.kind === "SUMMARY" ? "Nouveau résumé · " : ""}{stages[job.stage] || job.stage}</strong><span className="row"><span className="mono muted" style={{ fontSize: 12 }}>{job.progress}%</span><button type="button" className="btn small" onClick={() => job.status === "RUNNING" ? setConfirmCancel(true) : void cancelProcessing()} disabled={cancelling}>{cancelling ? "Annulation…" : "Annuler"}</button></span></div>{waiting && <p className="field-hint queue-hint">{waiting}</p>}<div className="progress-track" style={{ marginTop: 12 }} role="progressbar" aria-label="Progression du traitement" aria-valuemin={0} aria-valuemax={100} aria-valuenow={job.progress}><div style={{ width: `${job.progress}%` }}/></div>{notifications.supported && notifications.permission !== "denied" ? <label className="checkbox notify-toggle"><input type="checkbox" checked={notifications.wanted} onChange={event => void notifications.setNotify(event.target.checked)} /><span>Me prévenir à la fin</span></label> : <p className="field-hint notify-toggle">Notifications bloquées par le navigateur — le titre de l&apos;onglet indiquera la fin.</p>}</section>}

    {completed && !canPlay && !running && <div className="status-banner" role="status"><p>Les médias de cette vidéo ont été supprimés pour libérer de l&apos;espace : la transcription, le résumé et les exports restent disponibles.</p></div>}

    {canPlay && <section className={`media-layout${video.chapters.length ? " with-chapters" : ""}`} ref={playerRef}>
      <MediaPlayer videoId={video.id} mediaKind={video.media_kind} sourceAvailable={video.source_available} audioAvailable={video.audio_available} subtitles={completed} translation={video.translated_text ? video.target_language : null} mediaRef={mediaRef} onTime={onTime} />
      {!!video.chapters.length && <nav className="chapters card" aria-label="Chapitres"><h2>Chapitres</h2><ol>{video.chapters.map((chapter, index) => <li key={`${chapter.start_seconds}-${index}`}><button type="button" className={index === playingChapter ? "active" : undefined} onClick={() => seek(chapter.start_seconds)} aria-current={index === playingChapter ? "true" : undefined}><span className="mono">{formatDuration(chapter.start_seconds)}</span><span>{chapter.title}</span></button>{completed && <button type="button" className="chapter-clip" title="Extraire ce chapitre" aria-label={`Extraire le chapitre « ${chapter.title} »`} onClick={() => setClipRange({ start: chapter.start_seconds, end: video.chapters[index + 1]?.start_seconds ?? video.duration_seconds, title: chapter.title, nonce: Date.now() })}><Icon name="scissors" size={12} /></button>}</li>)}</ol></nav>}
    </section>}

    {completed && <SpeakersPanel videoId={video.id} speakers={video.speakers} canEdit={!running} diarizationError={video.diarization_error} onChanged={async message => { await loadVideo(); notify(message); }} onJobStarted={jobId => { setQueryJobId(jobId); void loadVideo(); }} />}

    <div className="tabs" role="tablist"><button className={`tab${tab === "summary" ? " active" : ""}`} onClick={() => setTab("summary")}>Résumé</button><button className={`tab${tab === "transcript" ? " active" : ""}`} onClick={() => setTab("transcript")}>Transcription</button><button className={`tab${tab === "translation" ? " active" : ""}`} onClick={() => setTab("translation")}>Traduction</button><button className={`tab${tab === "chat" ? " active" : ""}`} onClick={() => setTab("chat")}><Icon name="chat" size={14}/> Chat IA</button></div>

    {tab === "summary" && (video.summaries.length
      ? <SummaryPanel videoId={video.id} summaries={video.summaries} duration={video.duration_seconds} busy={running || !completed} outdated={video.summary_outdated} onSeek={canPlay ? seek : undefined} onChanged={async () => { await loadVideo(); notify("Résumé enregistré"); }} onJobStarted={jobId => { setQueryJobId(jobId); void loadVideo(); }} />
      : <div className="content-panel">{running ? "Résumé en cours…" : cancelledVideo ? "Aucun résumé : le traitement a été annulé." : video.status === "FAILED" ? "Aucun résumé : le traitement a échoué." : "Aucun résumé."}</div>)}

    {tab === "summary" && completed && !!video.summaries.length && <ActionsPanel videoId={video.id} onSeek={canPlay ? seek : undefined} refreshKey={`${video.summaries.at(-1)?.id}-${job?.status}`} busy={running} />}
    {tab === "summary" && completed && !!video.summaries.length && <SeriesChanges videoId={video.id} refreshKey={`${seriesKey}-${video.summaries.at(-1)?.id}`} />}

    {tab === "transcript" && <>
      {completed && <div className="row transcript-actions"><button className={`btn${correcting ? " selected" : ""}`} onClick={() => setCorrecting(value => !value)} disabled={running} aria-pressed={correcting}><Icon name="edit" size={14}/>{correcting ? "Terminer les corrections" : "Corriger la transcription"}</button>{running && <span className="field-hint">Corrections possibles à la fin du traitement en cours.</span>}</div>}
      {completed && correcting && !running && <GlossarySuggestions compact refreshKey={video.transcript_text} />}
      <TranscriptSearch label="la transcription" lines={transcriptLines} query={transcriptQuery} onQueryChange={setTranscriptQuery} empty="Transcription non disponible." playing={canPlay ? playingSegment : -1} onSeek={canPlay ? line => seek(line.start ?? 0) : undefined} onEdit={correcting && !running ? editSegment : undefined} speakers={video.speakers.map(speaker => ({ id: speaker.id, label: speaker.label }))} toolbar={correcting && !running ? <ReplaceBar videoId={video.id} disabled={running} onReplaced={async message => { await loadVideo(); notify(message); }} /> : undefined} />
    </>}

    {tab === "translation" && <>
      {video.translation_outdated && <div className="status-banner" role="status"><p>La transcription a été corrigée après cette traduction : elle sera refaite au prochain « Régénérer ».</p></div>}
      <TranscriptSearch label="la traduction" lines={translationLines} query={translationQuery} onQueryChange={setTranslationQuery} empty="Aucune traduction demandée." />
    </>}

    {tab === "chat" && <section className="chat card">{!!chatMessages.length && <a className="btn small chat-export" href={`${API}/videos/${video.id}/chat/export`}><Icon name="download" size={12}/>Exporter (.md)</a>}<p className="chat-intro">Posez une question sur le contenu de cette vidéo. Les réponses s’appuient uniquement sur sa transcription{video.chat_mode === "passages" ? ", dont les passages les plus proches de votre question sont retrouvés dans toute la vidéo" : ""}.</p>{video.chat_mode === "partial" && <div className="status-banner" role="status"><p>Indexation de cette longue vidéo en cours en arrière-plan : en attendant, les réponses ne portent que sur le début de la transcription.</p></div>}{!video.transcript_text ? <p className="muted">Le chat sera disponible dès la fin de la transcription.</p> : <><div className="chat-history" aria-live="polite">{!chatMessages.length && !pending && chatLoaded && <p className="muted">Aucune question pour le moment.</p>}{chatMessages.map(message => <article className={`chat-message ${message.role}`} key={message.id}><b>{message.role === "user" ? "Vous" : "Assistant"}</b><div>{message.role === "assistant" ? <TimestampText text={message.content} duration={video.duration_seconds} onSeek={canPlay ? seek : undefined} /> : message.content}</div>{message.interrupted && <p className="interrupted-note">Réponse interrompue : le début est conservé. Reposez la question pour l&apos;obtenir en entier.</p>}{message.role === "assistant" && <AnswerFeedback path={`/videos/${video.id}/chat/messages/${message.id}/feedback`} initial={message.feedback} />}</article>)}{pending && <><article className="chat-message user"><b>Vous</b><div>{pending.question}</div></article><article className="chat-message assistant" aria-busy="true"><b>Assistant</b><div>{pending.answer ? <TimestampText text={pending.answer} duration={video.duration_seconds} onSeek={canPlay ? seek : undefined} /> : <span className="muted typing">Réflexion…</span>}</div></article></>}</div><form className="chat-form" onSubmit={askQuestion}><label htmlFor="video-question">Votre question</label><textarea id="video-question" value={question} onChange={event => setQuestion(event.target.value)} maxLength={4000} placeholder="Ex. Quel point important est abordé à la fin ?" disabled={asking}/><button className="btn primary" disabled={asking || !question.trim()}>{asking ? "Réponse en cours…" : "Poser la question"}</button></form></>}</section>}

    {completed && canPlay && <ClipsPanel videoId={video.id} duration={video.duration_seconds} isAudio={video.media_kind === "audio" || !video.source_available} hasTranslation={!!video.translated_text} range={clipRange} currentTime={() => mediaRef.current ? mediaRef.current.currentTime : null} />}

    {completed && <div className="row exports"><a className="btn primary" href={`${API}/videos/${id}/exports/report.docx?transcript=${annex}`}><Icon name="download" size={14}/>Compte-rendu .docx</a><a className="btn" href={`${API}/videos/${id}/exports/report.pdf?transcript=${annex}`}><Icon name="download" size={14}/>Compte-rendu .pdf</a><label className="annex-choice">Annexe <select value={annex} onChange={event => setAnnex(event.target.value as typeof annex)} aria-label="Transcription jointe au compte-rendu"><option value="original">transcription</option>{video.translated_text && <option value="translation">traduction{video.translation_outdated ? " (avant correction)" : ""}</option>}<option value="none">aucune</option></select></label><a className="btn" href={`${API}/videos/${id}/exports/summary.md`}>Résumé .md</a><a className="btn" href={`${API}/videos/${id}/note.md`} title="Note Markdown pour Obsidian : métadonnées, résumé, actions à cocher, liens vers les personnes">Note Obsidian .md</a><a className="btn" href={`${API}/videos/${id}/email.eml`} title="S'ouvre dans Outlook ou Thunderbird comme un nouveau message à envoyer"><Icon name="mail" size={14}/>Brouillon d&apos;e-mail</a><a className="btn" href={`${API}/videos/${id}/exports/transcript.txt`}><Icon name="download" size={14}/>Transcript .txt</a><a className="btn" href={`${API}/videos/${id}/exports/transcript.srt`}>Sous-titres .srt</a><a className="btn" href={`${API}/videos/${id}/exports/transcript.vtt`}>Sous-titres .vtt</a>{!!video.chapters.length && <a className="btn" href={`${API}/videos/${id}/exports/chapters.txt`}>Chapitres .txt</a>}{video.translated_text && <><a className="btn" href={`${API}/videos/${id}/exports/translation.txt`}>Traduction .txt</a><a className="btn" href={`${API}/videos/${id}/exports/translation.srt`}>Sous-titres traduits .srt</a><a className="btn" href={`${API}/videos/${id}/exports/translation.vtt`}>Sous-titres traduits .vtt</a></>}<a className="btn" href={`${API}/videos/${id}/exports/metadata.json`}>Metadata .json</a></div>}

    {confirmCancel && <div className="modal-backdrop" role="dialog" aria-modal="true"><div className="modal"><h2>Annuler ce traitement ?</h2><p>{job?.kind === "SUMMARY" ? "La génération du nouveau résumé s’arrête ; le résumé actuel est conservé." : "Le travail en cours est abandonné. Vous pourrez relancer l’analyse plus tard."}</p><div className="modal-actions"><button className="btn" onClick={() => setConfirmCancel(false)} disabled={cancelling}>Continuer</button><button className="btn primary" style={{ background: "var(--danger)", borderColor: "var(--danger)" }} onClick={() => void cancelProcessing()} disabled={cancelling}>{cancelling ? "Annulation…" : "Annuler le traitement"}</button></div></div></div>}
    {confirmDelete && <div className="modal-backdrop" role="dialog" aria-modal="true"><div className="modal"><h2>Supprimer cette analyse ?</h2><p>Le fichier source, la transcription, le résumé et l’historique du chat seront supprimés définitivement.</p><div className="modal-actions"><button className="btn" onClick={() => setConfirmDelete(false)} disabled={deleting}>Annuler</button><button className="btn primary" style={{ background: "var(--danger)", borderColor: "var(--danger)" }} onClick={deleteVideo} disabled={deleting}>{deleting ? "Suppression…" : "Supprimer"}</button></div></div></div>}
    {toast && <div className="toast">✓ {toast}</div>}
  </div>;
}
