import { API, responseError } from "./api";

export type ChatMessage = { id: string; video_id: string; role: "user" | "assistant"; content: string; created_at: string };
export type Source = { n: number; video_id: string; title: string; start_seconds: number; end_seconds: number };
export type SourcesEvent = { sources: Source[]; searched: number; skipped: number };

/** POST a JSON body and read the Server-Sent Events of the answer (EventSource only does GET). */
async function postEvents(path: string, body: unknown, onEvent: (event: string, data: any) => boolean | void, signal?: AbortSignal) {
  const response = await fetch(`${API}${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
  });
  if (!response.ok) throw await responseError(response);
  if (!response.body) throw new Error("Réponse vide du serveur");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let event = "message", data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (!data) continue;
      const payload = JSON.parse(data);
      if (event === "error") throw new Error(payload.detail || "Assistant indisponible");
      // true: the answer is complete.
      if (onEvent(event, payload)) return;
    }
  }
  throw new Error("La réponse a été interrompue avant sa fin ; réessayez.");
}

/**
 * Ask a question on one video and receive the answer as it is written (n°16).
 * Events: `delta` pieces, then `done` with both saved messages.
 */
export async function streamChat(videoId: string, question: string, onDelta: (text: string) => void, signal?: AbortSignal): Promise<ChatMessage[]> {
  let saved: ChatMessage[] = [];
  await postEvents(`/videos/${videoId}/chat/stream`, { question }, (event, data) => {
    if (event === "delta") onDelta(data.text);
    else if (event === "done") { saved = data.messages; return true; }
  }, signal);
  return saved;
}

/** A question on several videos (n°19): numbered sources first, then the answer. */
export async function streamLibraryChat(
  body: { question: string; video_ids: string[]; history: { role: "user" | "assistant"; content: string }[] },
  onSources: (event: SourcesEvent) => void, onDelta: (text: string) => void, signal?: AbortSignal,
): Promise<string> {
  let answer = "";
  await postEvents("/library/chat/stream", body, (event, data) => {
    if (event === "sources") onSources(data);
    else if (event === "delta") onDelta(data.text);
    else if (event === "done") { answer = data.answer; return true; }
  }, signal);
  return answer;
}
