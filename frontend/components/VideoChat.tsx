"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { API, api } from "../lib/api";
import { ChatError, streamChat, type ChatMessage } from "../lib/chat";
import AnswerFeedback from "./AnswerFeedback";
import { Icon } from "./Icons";
import TimestampText from "./TimestampText";

/**
 * The chat tab of a video (n°16): its history, the answer streaming in. Kept
 * mounted while another tab is shown, so an answer being written goes on.
 */
export default function VideoChat({ videoId, active, hasTranscript, chatMode, duration, onSeek, onError }: {
  videoId: string; active: boolean; hasTranscript: boolean; chatMode: "none" | "full" | "passages" | "partial";
  duration: number; onSeek?: (seconds: number) => void; onError: (message: string) => void;
}) {
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [chatLoaded, setChatLoaded] = useState(false);
  // Question being answered and the answer received so far (n°16).
  const [pending, setPending] = useState<{ question: string; answer: string } | null>(null);
  const chatAbort = useRef<AbortController | null>(null);

  async function loadChat() {
    try { setChatMessages(await api<ChatMessage[]>(`/videos/${videoId}/chat/messages`)); setChatLoaded(true); onError(""); }
    catch (reason) { onError(String(reason)); }
  }
  // The answer streams in: the history is loaded once, no polling.
  useEffect(() => {
    if (!active || !hasTranscript) return;
    void loadChat();
  }, [active, hasTranscript, videoId]);
  // Leaving the page stops the generation (nothing is saved for an unfinished answer).
  useEffect(() => () => chatAbort.current?.abort(), []);

  async function askQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const value = question.trim(); if (!value || asking) return;
    setAsking(true); onError(""); setPending({ question: value, answer: "" }); setQuestion("");
    const controller = new AbortController(); chatAbort.current = controller;
    try {
      const saved = await streamChat(videoId, value, text => setPending(current => current && { ...current, answer: current.answer + text }), controller.signal);
      setChatMessages(current => [...current, ...saved]);
    } catch (reason) {
      if (controller.signal.aborted) return;
      if (reason instanceof ChatError && reason.saved) {
        // What was written is kept, flagged as interrupted.
        await loadChat();
        onError("La réponse a été interrompue ; ce qui a été écrit est conservé.");
      } else {
        // Nothing was saved: the question goes back into the box.
        onError(reason instanceof Error ? reason.message : String(reason)); setQuestion(value);
      }
    } finally { setPending(null); setAsking(false); }
  }

  if (!active) return null;
  return <section className="chat card">{!!chatMessages.length && <a className="btn small chat-export" href={`${API}/videos/${videoId}/chat/export`}><Icon name="download" size={12}/>Exporter (.md)</a>}<p className="chat-intro">Posez une question sur le contenu de cette vidéo. Les réponses s’appuient uniquement sur sa transcription{chatMode === "passages" ? ", dont les passages les plus proches de votre question sont retrouvés dans toute la vidéo" : ""}.</p>{chatMode === "partial" && <div className="status-banner" role="status"><p>Indexation de cette longue vidéo en cours en arrière-plan : en attendant, les réponses ne portent que sur le début de la transcription.</p></div>}{!hasTranscript ? <p className="muted">Le chat sera disponible dès la fin de la transcription.</p> : <><div className="chat-history" aria-live="polite">{!chatMessages.length && !pending && chatLoaded && <p className="muted">Aucune question pour le moment.</p>}{chatMessages.map(message => <article className={`chat-message ${message.role}`} key={message.id}><b>{message.role === "user" ? "Vous" : "Assistant"}</b><div>{message.role === "assistant" ? <TimestampText text={message.content} duration={duration} onSeek={onSeek} /> : message.content}</div>{message.interrupted && <p className="interrupted-note">Réponse interrompue : le début est conservé. Reposez la question pour l&apos;obtenir en entier.</p>}{message.role === "assistant" && <AnswerFeedback path={`/videos/${videoId}/chat/messages/${message.id}/feedback`} initial={message.feedback} />}</article>)}{pending && <><article className="chat-message user"><b>Vous</b><div>{pending.question}</div></article><article className="chat-message assistant" aria-busy="true"><b>Assistant</b><div>{pending.answer ? <TimestampText text={pending.answer} duration={duration} onSeek={onSeek} /> : <span className="muted typing">Réflexion…</span>}</div></article></>}</div><form className="chat-form" onSubmit={askQuestion}><label htmlFor="video-question">Votre question</label><textarea id="video-question" value={question} onChange={event => setQuestion(event.target.value)} maxLength={4000} placeholder="Ex. Quel point important est abordé à la fin ?" disabled={asking}/><button className="btn primary" disabled={asking || !question.trim()}>{asking ? "Réponse en cours…" : "Poser la question"}</button></form></>}</section>;
}
