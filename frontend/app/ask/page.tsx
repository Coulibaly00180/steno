"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, formatDuration } from "../../lib/api";
import { sourceLanguages } from "../../lib/analysis";
import { ChatError, streamLibraryChat, type Conversation, type ConversationDetail, type Source } from "../../lib/chat";
import { Icon } from "../../components/Icons";
import SourceText, { sourceHref } from "../../components/SourceText";
import type { VideoSummary } from "../../components/VideoTable";

type Turn = { question: string; answer: string; sources: Source[]; searched: number; skipped: number; done: boolean; interrupted: boolean };

function scopeLabel(params: URLSearchParams) {
  const parts: string[] = [];
  if (params.get("q")) parts.push(`recherche « ${params.get("q")} »`);
  if (params.get("tag")) parts.push(`tag « ${params.get("tag")} »`);
  const language = params.get("language");
  if (language) parts.push(`langue ${sourceLanguages.find(item => item.code === language)?.label.toLowerCase() || language}`);
  if (params.get("created_after")) parts.push(`importées depuis le ${new Date(params.get("created_after")!).toLocaleDateString("fr-FR")}`);
  return parts.length ? parts.join(" · ") : "toute la bibliothèque";
}

function relativeDate(value: string) {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 3600) return `Il y a ${Math.max(1, Math.floor(seconds / 60))} min`;
  if (seconds < 86400) return `Il y a ${Math.floor(seconds / 3600)} h`;
  if (seconds < 172800) return "Hier";
  return new Date(value).toLocaleDateString("fr-FR");
}

export default function AskPage() {
  const [params, setParams] = useState<URLSearchParams | null>(null);
  const [videos, setVideos] = useState<VideoSummary[] | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  // The open conversation: null until the first question of a new one is sent.
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [opened, setOpened] = useState<Conversation | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const loadConversations = useCallback(async () => {
    try { setConversations(await api<Conversation[]>("/library/conversations")); } catch { /* the history is optional */ }
  }, []);

  const openConversation = useCallback(async (id: string) => {
    abort.current?.abort();
    setError(""); setAsking(false);
    try {
      const detail = await api<ConversationDetail>(`/library/conversations/${id}`);
      const rebuilt: Turn[] = [];
      for (let index = 0; index < detail.messages.length; index++) {
        const message = detail.messages[index];
        if (message.role !== "user") continue;
        const answer = detail.messages[index + 1];
        rebuilt.push({
          question: message.content, answer: answer?.content ?? "", sources: answer?.sources ?? [], searched: 0, skipped: 0,
          done: true, interrupted: !!answer?.interrupted,
        });
      }
      setTurns(rebuilt); setConversationId(id); setOpened(detail);
      window.history.replaceState(null, "", `/ask?c=${id}`);
    } catch (reason) { setError(String(reason)); }
  }, []);

  useEffect(() => {
    const search = new URLSearchParams(window.location.search);
    const wanted = search.get("c");
    // Only processed videos can be questioned, whatever status the library showed.
    search.delete("status"); search.delete("c");
    setParams(search);
    const query = new URLSearchParams(search);
    query.set("status", "COMPLETED");
    api<VideoSummary[]>(`/videos?${query}`).then(setVideos).catch(reason => setError(String(reason)));
    void loadConversations();
    if (wanted) void openConversation(wanted);
    return () => abort.current?.abort();
  }, [loadConversations, openConversation]);

  useEffect(() => { bottom.current?.scrollIntoView({ block: "nearest" }); }, [turns]);

  function update(index: number, change: Partial<Turn>) {
    setTurns(current => current.map((turn, position) => position === index ? { ...turn, ...change } : turn));
  }

  function startNew() {
    abort.current?.abort();
    setTurns([]); setConversationId(null); setOpened(null); setError(""); setAsking(false);
    window.history.replaceState(null, "", "/ask");
  }

  async function removeConversation(id: string) {
    try {
      await api(`/library/conversations/${id}`, { method: "DELETE" });
      setConfirmDelete(null);
      if (id === conversationId) startNew();
      await loadConversations();
    } catch (reason) { setError(String(reason)); }
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    const value = question.trim();
    const canAsk = conversationId ? true : !!videos?.length;
    if (!value || asking || !canAsk) return;
    const index = turns.length;
    const startedNew = conversationId === null;
    setTurns(current => [...current, { question: value, answer: "", sources: [], searched: 0, skipped: 0, done: false, interrupted: false }]);
    setQuestion(""); setAsking(true); setError("");
    const controller = new AbortController(); abort.current = controller;
    let answer = "";
    try {
      await streamLibraryChat(
        conversationId
          ? { question: value, conversation_id: conversationId }
          : { question: value, video_ids: videos!.map(video => video.id), scope: params ? scopeLabel(params) : null },
        event => {
          update(index, { sources: event.sources, searched: event.searched, skipped: event.skipped });
          setConversationId(event.conversation_id);
          window.history.replaceState(null, "", `/ask?c=${event.conversation_id}`);
        },
        text => { answer += text; update(index, { answer }); },
        controller.signal,
      );
      update(index, { done: true });
      await loadConversations();
    } catch (reason) {
      if (controller.signal.aborted) return;
      if (reason instanceof ChatError && reason.saved) {
        // What was written is kept by the server, flagged as interrupted.
        update(index, { done: true, interrupted: true });
        setError("La réponse a été interrompue ; ce qui a été écrit est conservé.");
        await loadConversations();
      } else {
        // Nothing was kept: the question goes back into the box.
        setTurns(current => current.slice(0, index));
        setQuestion(value);
        if (startedNew) { setConversationId(null); window.history.replaceState(null, "", "/ask"); }
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally { setAsking(false); }
  }

  const count = opened ? opened.video_count : videos?.length ?? 0;
  const scope = opened ? (opened.scope || "toute la bibliothèque") : params ? scopeLabel(params) : "";
  const canAsk = conversationId ? true : count > 0;

  return <div className="page wide">
    <header className="page-header"><h1>Questions sur vos vidéos</h1><p>Posez une question : la réponse s’appuie sur les passages les plus pertinents de plusieurs vidéos, avec leurs sources. Vos conversations sont conservées.</p></header>
    <div className="ask-layout">
      <div>
        <div className="ask-scope card">
          <div><strong>{videos === null && !opened ? "Chargement…" : `${count} vidéo${count > 1 ? "s" : ""}${opened ? "" : ` analysée${count > 1 ? "s" : ""}`}`}</strong> <span className="muted">· {scope}</span></div>
          <Link className="text-link" href="/library">Choisir d’autres vidéos dans la bibliothèque</Link>
        </div>
        {videos !== null && !count && !opened && <div className="status-banner" role="status"><p>Aucune vidéo analysée dans ce périmètre.</p></div>}

        <section className="ask-thread" aria-live="polite">
          {turns.map((turn, index) => <article className="ask-turn" key={index}>
            <div className="chat-message user"><b>Vous</b><div>{turn.question}</div></div>
            <div className="chat-message assistant" aria-busy={!turn.done}>
              <b>Assistant</b>
              <div>{turn.answer ? <SourceText text={turn.answer} sources={turn.sources} /> : <span className="muted typing">{turn.done ? "Pas de réponse." : turn.sources.length ? "Rédaction…" : "Recherche des passages…"}</span>}</div>
              {turn.interrupted && <p className="interrupted-note">Réponse interrompue : le début est conservé. Reposez la question pour l&apos;obtenir en entier.</p>}
              {!!turn.sources.length && <details className="ask-sources" open={turn.done && index === turns.length - 1}>
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
          <textarea id="library-question" value={question} onChange={event => setQuestion(event.target.value)} maxLength={4000} placeholder="Ex. : Quelles décisions ont été prises sur le budget, et dans quelles réunions ?" disabled={asking || !canAsk} />
          <button className="btn primary" disabled={asking || !question.trim() || !canAsk}>{asking ? "Réponse en cours…" : "Poser la question"}</button>
        </form>
      </div>

      <aside className="ask-history" aria-label="Conversations">
        <div className="spread"><h2>Conversations</h2><button type="button" className="btn small" onClick={startNew} disabled={!turns.length && !conversationId}><Icon name="chat" size={12}/>Nouvelle</button></div>
        {!conversations.length ? <p className="field-hint">Aucune conversation enregistrée.</p> : <ul>{conversations.map(conversation => <li key={conversation.id} className={`conversation-item${conversation.id === conversationId ? " active" : ""}`}>
          <button type="button" className="conversation-open" onClick={() => void openConversation(conversation.id)}><span className="conversation-title">{conversation.title}</span><span className="conversation-meta">{conversation.video_count} vidéo{conversation.video_count > 1 ? "s" : ""} · {relativeDate(conversation.updated_at)}</span></button>
          {confirmDelete === conversation.id
            ? <button type="button" className="btn small danger" onClick={() => void removeConversation(conversation.id)} onBlur={() => setConfirmDelete(null)} autoFocus>Supprimer ?</button>
            : <button type="button" className="icon-btn" aria-label={`Supprimer la conversation « ${conversation.title} »`} onClick={() => setConfirmDelete(conversation.id)}><Icon name="close" size={12}/></button>}
        </li>)}</ul>}
      </aside>
    </div>
  </div>;
}
