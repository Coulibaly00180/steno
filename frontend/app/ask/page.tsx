"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, formatDuration } from "../../lib/api";
import { sourceLanguages } from "../../lib/analysis";
import { streamLibraryChat, type Source } from "../../lib/chat";
import SourceText, { sourceHref } from "../../components/SourceText";
import type { VideoSummary } from "../../components/VideoTable";

type Turn = { question: string; answer: string; sources: Source[]; searched: number; skipped: number; done: boolean };

// Earlier exchanges sent back for follow-up questions; long answers are cut.
const HISTORY_TURNS = 3;
const HISTORY_ANSWER_CHARS = 2000;

function scopeLabel(params: URLSearchParams) {
  const parts: string[] = [];
  if (params.get("q")) parts.push(`recherche « ${params.get("q")} »`);
  if (params.get("tag")) parts.push(`tag « ${params.get("tag")} »`);
  const language = params.get("language");
  if (language) parts.push(`langue ${sourceLanguages.find(item => item.code === language)?.label.toLowerCase() || language}`);
  if (params.get("created_after")) parts.push(`importées depuis le ${new Date(params.get("created_after")!).toLocaleDateString("fr-FR")}`);
  return parts.length ? parts.join(" · ") : "toute la bibliothèque";
}

export default function AskPage() {
  const [params, setParams] = useState<URLSearchParams | null>(null);
  const [videos, setVideos] = useState<VideoSummary[] | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const search = new URLSearchParams(window.location.search);
    // Only processed videos can be questioned, whatever status the library showed.
    search.delete("status");
    setParams(search);
    const query = new URLSearchParams(search);
    query.set("status", "COMPLETED");
    api<VideoSummary[]>(`/videos?${query}`).then(setVideos).catch(reason => setError(String(reason)));
    return () => abort.current?.abort();
  }, []);

  useEffect(() => { bottom.current?.scrollIntoView({ block: "nearest" }); }, [turns]);

  function update(index: number, change: Partial<Turn>) {
    setTurns(current => current.map((turn, position) => position === index ? { ...turn, ...change } : turn));
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    const value = question.trim();
    if (!value || asking || !videos?.length) return;
    const history = turns.filter(turn => turn.done).slice(-HISTORY_TURNS).flatMap(turn => [
      { role: "user" as const, content: turn.question },
      { role: "assistant" as const, content: turn.answer.slice(0, HISTORY_ANSWER_CHARS) },
    ]);
    const index = turns.length;
    setTurns(current => [...current, { question: value, answer: "", sources: [], searched: 0, skipped: 0, done: false }]);
    setQuestion(""); setAsking(true); setError("");
    const controller = new AbortController(); abort.current = controller;
    let answer = "";
    try {
      await streamLibraryChat(
        { question: value, video_ids: videos.map(video => video.id), history },
        sources => update(index, sources),
        text => { answer += text; update(index, { answer }); },
        controller.signal,
      );
      update(index, { done: true });
    } catch (reason) {
      if (controller.signal.aborted) return;
      // Nothing is kept for a failed question: it goes back into the box.
      setTurns(current => current.slice(0, index));
      setQuestion(value);
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setAsking(false); }
  }

  const count = videos?.length ?? 0;
  return <div className="page">
    <header className="page-header"><h1>Questions sur vos vidéos</h1><p>Posez une question : la réponse s’appuie sur les passages les plus pertinents de plusieurs vidéos, avec leurs sources.</p></header>
    <div className="ask-scope card">
      <div><strong>{videos === null ? "Chargement…" : `${count} vidéo${count > 1 ? "s" : ""} analysée${count > 1 ? "s" : ""}`}</strong> <span className="muted">· {params ? scopeLabel(params) : ""}</span></div>
      <Link className="text-link" href="/library">Choisir d’autres vidéos dans la bibliothèque</Link>
    </div>
    {videos !== null && !count && <div className="status-banner" role="status"><p>Aucune vidéo analysée dans ce périmètre.</p></div>}

    <section className="ask-thread" aria-live="polite">
      {turns.map((turn, index) => <article className="ask-turn" key={index}>
        <div className="chat-message user"><b>Vous</b><div>{turn.question}</div></div>
        <div className="chat-message assistant" aria-busy={!turn.done}>
          <b>Assistant</b>
          <div>{turn.answer ? <SourceText text={turn.answer} sources={turn.sources} /> : <span className="muted typing">{turn.sources.length ? "Rédaction…" : "Recherche des passages…"}</span>}</div>
          {!!turn.sources.length && <details className="ask-sources" open={turn.done}>
            <summary>{turn.sources.length} passage{turn.sources.length > 1 ? "s" : ""} consulté{turn.sources.length > 1 ? "s" : ""} dans {new Set(turn.sources.map(source => source.video_id)).size} vidéo{new Set(turn.sources.map(source => source.video_id)).size > 1 ? "s" : ""}</summary>
            <ol>{turn.sources.map(source => <li key={source.n}><Link href={sourceHref(source)}><span className="citation">{source.n}</span> {source.title} <span className="mono muted">{formatDuration(source.start_seconds)}</span></Link></li>)}</ol>
            {turn.skipped > 0 && <p className="field-hint">{turn.skipped} vidéo{turn.skipped > 1 ? "s" : ""} pas encore indexée{turn.skipped > 1 ? "s" : ""} (indexation en cours en arrière-plan) : non consultée{turn.skipped > 1 ? "s" : ""}.</p>}
          </details>}
        </div>
      </article>)}
      <div ref={bottom} />
    </section>

    {error && <div className="error" role="alert">{error}</div>}
    <form className="chat-form card" onSubmit={ask}>
      <label htmlFor="library-question">Votre question</label>
      <textarea id="library-question" value={question} onChange={event => setQuestion(event.target.value)} maxLength={4000} placeholder="Ex. : Quelles décisions ont été prises sur le budget, et dans quelles réunions ?" disabled={asking || !count} />
      <button className="btn primary" disabled={asking || !question.trim() || !count}>{asking ? "Réponse en cours…" : "Poser la question"}</button>
    </form>
  </div>;
}
