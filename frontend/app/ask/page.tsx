"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { API, api, formatDuration } from "../../lib/api";
import { sourceLanguages } from "../../lib/analysis";
import { ChatError, streamLibraryChat, type Conversation, type ConversationDetail, type Source } from "../../lib/chat";
import AnswerFeedback from "../../components/AnswerFeedback";
import { Icon } from "../../components/Icons";
import SourceText, { sourceHref } from "../../components/SourceText";
import type { VideoSummary } from "../../components/VideoTable";

type Turn = { question: string; answer: string; sources: Source[]; searched: number; skipped: number; done: boolean; interrupted: boolean; messageId?: string | null; feedback?: number | null };

function scopeLabel(params: URLSearchParams, entityName?: string) {
  const parts: string[] = [];
  if (params.get("entity")) parts.push(`vidéos citant « ${entityName || "…"} »`);
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
  const [entityName, setEntityName] = useState("");
  // n°18: search the conversations, keep only those with an answer rated wrong, rename.
  const [historyQuery, setHistoryQuery] = useState("");
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const loadConversations = useCallback(async () => {
    const query = new URLSearchParams();
    if (historyQuery.trim()) query.set("q", historyQuery.trim());
    if (flaggedOnly) query.set("flagged", "true");
    try { setConversations(await api<Conversation[]>(`/library/conversations?${query}`)); } catch { /* the history is optional */ }
  }, [historyQuery, flaggedOnly]);

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
          done: true, interrupted: !!answer?.interrupted, messageId: answer?.id ?? null, feedback: answer?.feedback ?? null,
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
    const entity = search.get("entity");
    if (entity) api<{ name: string }>(`/entities/${entity}`).then(found => setEntityName(found.name)).catch(() => setEntityName(""));
    if (wanted) void openConversation(wanted);
    return () => abort.current?.abort();
  }, [openConversation]);

  // The history follows its search box (debounced) and its "signalées" filter.
  useEffect(() => {
    const timer = window.setTimeout(() => void loadConversations(), 250);
    return () => window.clearTimeout(timer);
  }, [loadConversations]);

  async function rename(id: string, title: string) {
    const value = title.trim();
    setRenaming(null);
    if (!value) return;
    try {
      await api(`/library/conversations/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: value }) });
      if (opened?.id === id) setOpened({ ...opened, title: value });
      await loadConversations();
    } catch (reason) { setError(String(reason)); }
  }

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
      const result = await streamLibraryChat(
        conversationId
          ? { question: value, conversation_id: conversationId }
          : { question: value, video_ids: videos!.map(video => video.id), scope: params ? scopeLabel(params, entityName) : null },
        event => {
          update(index, { sources: event.sources, searched: event.searched, skipped: event.skipped });
          setConversationId(event.conversation_id);
          window.history.replaceState(null, "", `/ask?c=${event.conversation_id}`);
        },
        text => { answer += text; update(index, { answer }); },
        controller.signal,
      );
      update(index, { done: true, messageId: result.messageId });
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
  const scope = opened ? (opened.scope || "toute la bibliothèque") : params ? scopeLabel(params, entityName) : "";
  const canAsk = conversationId ? true : count > 0;

  return <div className="page wide">
    <header className="page-header"><h1>Questions sur vos vidéos</h1><p>Posez une question : la réponse s’appuie sur les passages les plus pertinents de plusieurs vidéos, avec leurs sources. Vos conversations sont conservées.</p></header>
    <div className="ask-layout">
      <div>
        <div className="ask-scope card">
          <div><strong>{videos === null && !opened ? "Chargement…" : `${count} vidéo${count > 1 ? "s" : ""}${opened ? "" : ` analysée${count > 1 ? "s" : ""}`}`}</strong> <span className="muted">· {scope}</span></div>
          <Link className="text-link" href="/library">Choisir d’autres vidéos dans la bibliothèque</Link>
          {conversationId && <div className="conversation-tools">
            {renaming === conversationId
              ? <input className="rename-input" defaultValue={opened?.title ?? turns[0]?.question ?? ""} maxLength={120} autoFocus aria-label="Titre de la conversation" onBlur={event => void rename(conversationId, event.target.value)} onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); if (event.key === "Escape") setRenaming(null); }} />
              : <button type="button" className="btn small" onClick={() => setRenaming(conversationId)}><Icon name="edit" size={12}/>Renommer</button>}
            <a className="btn small" href={`${API}/library/conversations/${conversationId}/export`}><Icon name="download" size={12}/>Exporter (.md)</a>
          </div>}
        </div>
        {videos !== null && !count && !opened && <div className="status-banner" role="status"><p>Aucune vidéo analysée dans ce périmètre.</p></div>}

        <section className="ask-thread" aria-live="polite">
          {turns.map((turn, index) => <article className="ask-turn" key={index}>
            <div className="chat-message user"><b>Vous</b><div>{turn.question}</div></div>
            <div className="chat-message assistant" aria-busy={!turn.done}>
              <b>Assistant</b>
              <div>{turn.answer ? <SourceText text={turn.answer} sources={turn.sources} /> : <span className="muted typing">{turn.done ? "Pas de réponse." : turn.sources.length ? "Rédaction…" : "Recherche des passages…"}</span>}</div>
              {turn.interrupted && <p className="interrupted-note">Réponse interrompue : le début est conservé. Reposez la question pour l&apos;obtenir en entier.</p>}
              {turn.done && turn.messageId && <AnswerFeedback key={turn.messageId} path={`/library/messages/${turn.messageId}/feedback`} initial={turn.feedback} />}
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
        <div className="history-filters"><input type="search" value={historyQuery} onChange={event => setHistoryQuery(event.target.value)} placeholder="Rechercher…" aria-label="Rechercher dans les conversations" maxLength={200} /><label className="checkbox"><input type="checkbox" checked={flaggedOnly} onChange={event => setFlaggedOnly(event.target.checked)} /><span>Réponses signalées</span></label></div>
        {!conversations.length ? <p className="field-hint">{historyQuery || flaggedOnly ? "Aucune conversation ne correspond." : "Aucune conversation enregistrée."}</p> : <ul>{conversations.map(conversation => <li key={conversation.id} className={`conversation-item${conversation.id === conversationId ? " active" : ""}`}>
          <button type="button" className="conversation-open" onClick={() => void openConversation(conversation.id)}><span className="conversation-title">{conversation.flagged && <span className="flag-dot" title="Une réponse est signalée comme incorrecte" />}{conversation.title}</span><span className="conversation-meta">{conversation.video_count} vidéo{conversation.video_count > 1 ? "s" : ""} · {relativeDate(conversation.updated_at)}</span></button>
          {confirmDelete === conversation.id
            ? <button type="button" className="btn small danger" onClick={() => void removeConversation(conversation.id)} onBlur={() => setConfirmDelete(null)} autoFocus>Supprimer ?</button>
            : <button type="button" className="icon-btn" aria-label={`Supprimer la conversation « ${conversation.title} »`} onClick={() => setConfirmDelete(conversation.id)}><Icon name="close" size={12}/></button>}
        </li>)}</ul>}
      </aside>
    </div>
  </div>;
}
