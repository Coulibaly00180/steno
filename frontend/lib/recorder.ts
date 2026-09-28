import { API, responseError } from "./api";

// n°10: what to record. "tab": the sound of a browser tab or window (a video call
// in the browser); "both": that sound and the microphone, on two channels
// (feuille de route n° 3, phase 4): the microphone on the left, the tab on the
// right, so the server knows which side said what and removes the echo.
export type RecordingSource = "mic" | "tab" | "both";

// `inputs`: the captured tracks; they end when the shared tab closes or the microphone is unplugged.
// `sides`: the stream is stereo, microphone left and tab right; `sideLevels` then measure each side.
export type Capture = {
  stream: MediaStream; inputs: MediaStreamTrack[]; level: AnalyserNode; sides: boolean;
  sideLevels: { you: AnalyserNode; others: AnalyserNode } | null; stop: () => void;
};

// Opus in WebM (Chrome, Edge) or Ogg (Firefox), AAC in MP4 (Safari): all accepted by the API.
const MIME_TYPES = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/mp4"];
// A chunk every few seconds: little is lost if the tab closes, and the live transcript follows closely.
export const CHUNK_MS = 4000;
const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000, 15000];

export function recordingSupported(): boolean {
  return typeof window !== "undefined" && typeof MediaRecorder !== "undefined" && !!navigator.mediaDevices?.getUserMedia;
}

export function tabCaptureSupported(): boolean {
  return recordingSupported() && !!navigator.mediaDevices?.getDisplayMedia;
}

export function pickMimeType(): string | null {
  return MIME_TYPES.find(type => MediaRecorder.isTypeSupported(type)) ?? null;
}

async function microphone(): Promise<MediaStream> {
  try {
    return await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  } catch {
    throw new Error("Accès au micro refusé : autorisez-le dans la barre d'adresse du navigateur.");
  }
}

async function tabAudio(): Promise<MediaStream> {
  let display: MediaStream;
  try {
    // Browsers only share a tab's sound along with its picture: the picture is dropped at once.
    display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
  } catch {
    throw new Error("Partage annulé : choisissez l'onglet ou la fenêtre de la réunion.");
  }
  display.getVideoTracks().forEach(track => track.stop());
  if (!display.getAudioTracks().length) {
    throw new Error("Aucun son partagé : cochez « Partager l'audio » (ou « l'audio de l'onglet ») dans la fenêtre de partage.");
  }
  return new MediaStream(display.getAudioTracks());
}

/** The stream to record, through an AudioContext, with an analyser for the level meter. */
export async function openCapture(source: RecordingSource): Promise<Capture> {
  const inputs: MediaStream[] = [];
  let tab: MediaStream | null = null, mic: MediaStream | null = null;
  try {
    if (source === "tab" || source === "both") { tab = await tabAudio(); inputs.push(tab); }
    if (source === "mic" || source === "both") { mic = await microphone(); inputs.push(mic); }
  } catch (error) {
    inputs.forEach(stream => stream.getTracks().forEach(track => track.stop()));
    throw error;
  }
  const context = new AudioContext();
  const destination = context.createMediaStreamDestination();
  const level = context.createAnalyser();
  level.fftSize = 1024;
  const sides = !!(tab && mic);
  // Each side folded to mono first: a stereo tab must not spill into the microphone's channel.
  const mono = (stream: MediaStream) => {
    const node = context.createGain();
    node.channelCount = 1;
    node.channelCountMode = "explicit";
    node.channelInterpretation = "speakers";
    context.createMediaStreamSource(stream).connect(node);
    node.connect(level);
    return node;
  };
  let sideLevels: Capture["sideLevels"] = null;
  if (sides && tab && mic) {
    destination.channelCount = 2;
    const merger = context.createChannelMerger(2);
    const you = mono(mic), others = mono(tab);
    you.connect(merger, 0, 0);
    others.connect(merger, 0, 1);
    merger.connect(destination);
    // One meter per side: a tab shared without its sound shows at once, before the meeting starts.
    sideLevels = { you: context.createAnalyser(), others: context.createAnalyser() };
    sideLevels.you.fftSize = sideLevels.others.fftSize = 1024;
    you.connect(sideLevels.you);
    others.connect(sideLevels.others);
  } else {
    for (const input of inputs) mono(input).connect(destination);
  }
  return {
    stream: destination.stream,
    inputs: inputs.flatMap(input => input.getAudioTracks()),
    level,
    sides,
    sideLevels,
    stop: () => {
      inputs.forEach(stream => stream.getTracks().forEach(track => track.stop()));
      // Called when stopping, then again when leaving the page.
      if (context.state !== "closed") void context.close().catch(() => undefined);
    },
  };
}

/** 0 to 1: loudness of what is recorded now. */
export function readLevel(analyser: AnalyserNode, buffer: Uint8Array<ArrayBuffer>): number {
  analyser.getByteTimeDomainData(buffer);
  let sum = 0;
  for (const value of buffer) { const centred = (value - 128) / 128; sum += centred * centred; }
  return Math.min(1, Math.sqrt(sum / buffer.length) * 4);
}

const wait = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));

/**
 * Sends the chunks in order, one at a time, retrying through short network cuts.
 * The server acknowledges a chunk sent twice, so a retry after a lost answer is safe.
 */
export class ChunkUploader {
  private queue: Blob[] = [];
  private next = 0;
  private running: Promise<void> | null = null;
  private failure: Error | null = null;
  sentBytes = 0;

  constructor(private recordingId: string, private onChange: () => void) {}

  get pending() { return this.queue.length; }
  get error() { return this.failure; }

  push(chunk: Blob) {
    if (!chunk.size) return;
    this.queue.push(chunk);
    this.onChange();
    if (!this.running) this.running = this.drain().finally(() => { this.running = null; });
  }

  private async drain() {
    while (this.queue.length) {
      const chunk = this.queue[0];
      await this.send(chunk);
      this.queue.shift();
      this.next += 1;
      this.sentBytes += chunk.size;
      this.failure = null;
      this.onChange();
    }
  }

  private async send(chunk: Blob) {
    for (let attempt = 0; ; attempt += 1) {
      try {
        const response = await fetch(`${API}/recordings/${this.recordingId}/chunks/${this.next}`, { method: "PUT", body: chunk });
        if (response.ok) return;
        const error = await responseError(response);
        // 4xx: the recording was stopped elsewhere, or is too large; retrying will not help.
        if (response.status < 500) { this.failure = error; this.onChange(); throw error; }
        this.failure = error;
      } catch (error) {
        if (this.failure && this.failure === error) throw error;
        this.failure = new Error("Connexion perdue : l'enregistrement continue, l'envoi reprendra tout seul.");
      }
      this.onChange();
      await wait(RETRY_DELAYS_MS[Math.min(attempt, RETRY_DELAYS_MS.length - 1)]);
    }
  }

  /** Resolves once every chunk is on the server. */
  async flush() {
    while (this.running) await this.running;
  }
}
