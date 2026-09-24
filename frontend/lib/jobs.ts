import { api } from "./api";

export type Job = {
  id: string; video_id: string; stage: string; status: string; progress: number; error?: string | null; kind?: string;
  // Active jobs only (n°13): 0 = running, n = n-th in the queue.
  queue_position?: number | null;
  // Until the end of the job; null until a job of the same kind has completed once.
  estimated_seconds_remaining?: number | null;
};

export const TERMINAL_STATUSES = ["COMPLETED", "FAILED", "CANCELLED"];
export const isActive = (job?: Job | null) => !!job && (job.status === "QUEUED" || job.status === "RUNNING");

export const stageLabels: Record<string, string> = {
  QUEUED: "En attente", STARTING: "Démarrage", DOWNLOADING: "Téléchargement", EXTRACTING_AUDIO: "Extraction audio", TRANSCRIBING: "Transcription",
  DIARIZING: "Identification des intervenants", TRANSCRIBED: "Transcription terminée", TRANSLATING: "Traduction", SUMMARIZING_CHUNKS: "Résumé par blocs",
  SUMMARIZING_GROUPS: "Consolidation des blocs", SUMMARIZING_FINAL: "Résumé final", GENERATING_EXPORTS: "Génération des exports",
  INDEXING: "Indexation pour les questions", COMPACTING: "Conversion en audio seul",
  COMPLETED: "Terminé", FAILED: "Échec", CANCELLED: "Annulé",
};

/** Rounded up: announcing 10 min and taking 12 disappoints more than the reverse. */
export function formatRemaining(seconds: number) {
  if (seconds < 60) return "moins d'une minute";
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `~${minutes} min`;
  const hours = Math.floor(minutes / 60), rest = minutes % 60;
  return rest ? `~${hours} h ${String(rest).padStart(2, "0")}` : `~${hours} h`;
}

/** "3ᵉ dans la file · fin dans ~25 min", or null when there is nothing to say. */
export function queueText(job?: Job | null): string | null {
  if (!isActive(job) || job!.queue_position == null) return null;
  const remaining = job!.estimated_seconds_remaining;
  if (job!.status === "RUNNING") {
    if (remaining == null) return null;
    return remaining === 0 ? "Bientôt terminé" : `Fin estimée dans ${formatRemaining(remaining)}`;
  }
  const position = job!.queue_position === 1 ? "Prochain dans la file" : `${job!.queue_position}ᵉ dans la file`;
  if (remaining == null) return `${position} · estimation après un premier traitement`;
  return `${position} · fin dans ${formatRemaining(remaining)}`;
}

export function cancelJob(jobId: string) {
  return api<Job>(`/jobs/${jobId}/cancel`, { method: "POST" });
}
