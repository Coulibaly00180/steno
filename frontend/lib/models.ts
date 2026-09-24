import { API, responseError } from "./api";

export type Pull = { status: string; completed?: number | null; total?: number | null; error?: string };

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** Read the pull's Server-Sent Events (EventSource only does GET). */
export async function pullModel(name: string, onProgress: (pull: Pull) => void) {
  const response = await fetch(`${API}/models/llm/pull`, json("POST", { name }));
  if (!response.ok || !response.body) throw await responseError(response);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const event = block.match(/^event: (.*)$/m)?.[1];
      const data = JSON.parse(block.match(/^data: (.*)$/m)?.[1] ?? "{}");
      if (event === "error") throw new Error(data.detail);
      if (event === "done") return;
      onProgress(data);
    }
  }
}
