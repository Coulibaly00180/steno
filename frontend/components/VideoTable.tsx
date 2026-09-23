"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { API, api, formatDuration } from "../lib/api";
import { cancelJob, isActive, queueText, stageLabels, type Job } from "../lib/jobs";
import { Icon } from "./Icons";

export type VideoSummary = {
  id: string; original_filename: string; duration_seconds: number; size_bytes: number; status: string;
  detected_language?: string; target_language?: string; created_at: string; tags?: string[]; job?: Job | null;
};

function statusInfo(status: string) {
  if (status === "COMPLETED") return { label: "Terminé", className: "status-completed" };
  if (status === "FAILED") return { label: "Échec", className: "status-failed" };
  if (status === "CANCELLED") return { label: "Annulé", className: "status-cancelled" };
  if (status === "QUEUED") return { label: "En attente", className: "status-queued" };
  return { label: "En cours", className: "status-running" };
}

function relativeDate(value: string) {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return "À l'instant";
  if (seconds < 3600) return `Il y a ${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `Il y a ${Math.floor(seconds / 3600)} h`;
  if (seconds < 172800) return "Hier";
  if (seconds < 604800) return `Il y a ${Math.floor(seconds / 86400)} j`;
  return new Date(value).toLocaleDateString("fr-FR");
}

export default function VideoTable({ videos, onChanged, onTagClick, empty = "Aucune analyse pour le moment." }: {
  videos: VideoSummary[]; onChanged?: () => void | Promise<void>; onTagClick?: (tag: string) => void; empty?: string;
}) {
  const router = useRouter();
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<VideoSummary | null>(null);
  const [cancelTarget, setCancelTarget] = useState<VideoSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");

  function notify(message: string) { setToast(message); window.setTimeout(() => setToast(""), 2400); }

  async function removeVideo() {
    if (!deleteTarget) return;
    setBusy(true); setError("");
    try {
      await api(`/videos/${deleteTarget.id}`, { method: "DELETE" });
      setDeleteTarget(null); notify("Vidéo supprimée");
      await onChanged?.();
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  }

  async function cancel(video: VideoSummary) {
    if (!video.job) return;
    setBusy(true); setError("");
    try {
      await cancelJob(video.job.id);
      setCancelTarget(null); notify("Traitement annulé");
      await onChanged?.();
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  }

  async function retry(video: VideoSummary) {
    setOpenMenu(null); setError("");
    try {
      const job = await api<{ id: string }>(`/videos/${video.id}/retry`, { method: "POST" });
      router.push(`/videos/${video.id}?job=${job.id}`);
    } catch (reason) { setError(String(reason)); }
  }

  if (!videos.length) return <div className="analysis-table"><div className="empty-state">{empty}</div></div>;

  return <>
    {error && <div className="error" role="alert">{error}</div>}
    <div className="analysis-table">
      <div className="analysis-head"><div>Fichier</div><div>Durée</div><div className="analysis-lang">Langue</div><div>Statut</div><div className="analysis-date">Date</div><div /></div>
      {videos.map(video => {
        const status = statusInfo(video.status);
        const audio = /\.(mp3|m4a|wav|flac|ogg|oga|opus)$/i.test(video.original_filename);
        const language = video.target_language ? `${(video.detected_language || "?").toUpperCase()} → ${video.target_language.slice(0, 2).toUpperCase()}` : video.detected_language || "—";
        const job = video.job;
        const active = isActive(job);
        const running = active && job!.status === "RUNNING";
        // A regeneration runs on a completed video: its row stays "Terminé".
        const showProgress = running && video.status !== "COMPLETED";
        const waiting = queueText(job);
        return <div className="analysis-row" key={video.id}>
          <div className="analysis-file">
            <Link href={`/videos/${video.id}`} className="analysis-file-link"><span className="analysis-file-icon"><Icon name={audio ? "audio" : "video"} size={14}/></span><span className="analysis-name">{video.original_filename}</span></Link>
            {!!video.tags?.length && <div className="tag-list">{video.tags.map(tag => onTagClick
              ? <button type="button" className="tag-chip" key={tag} onClick={() => onTagClick(tag)} title={`Filtrer sur « ${tag} »`}>{tag}</button>
              : <span className="tag-chip" key={tag}>{tag}</span>)}</div>}
          </div>
          <Link href={`/videos/${video.id}`} className="analysis-cell mono">{formatDuration(video.duration_seconds).replace(/^00:/, "")}</Link>
          <Link href={`/videos/${video.id}`} className="analysis-cell analysis-lang">{language}</Link>
          <Link href={`/videos/${video.id}`} className="analysis-cell"><span className={`status-badge ${status.className}`}><i className={showProgress ? "pulse" : ""}/>{status.label}</span>
            {showProgress && <div className="mini-progress"><div className="mini-track"><i style={{ width: `${job!.progress}%` }}/></div><div className="mini-label">{stageLabels[job!.stage] || job!.stage} · {job!.progress}%</div></div>}
            {active && video.status === "COMPLETED" && <div className="mini-label">Nouveau résumé · {job!.status === "QUEUED" ? "en attente" : `${job!.progress}%`}</div>}
            {waiting && <div className="mini-label queue-label">{waiting}</div>}
          </Link>
          <Link href={`/videos/${video.id}`} className="analysis-cell analysis-date">{relativeDate(video.created_at)}</Link>
          <div className="row-menu"><button className="icon-btn" aria-label={`Actions pour ${video.original_filename}`} onClick={() => setOpenMenu(openMenu === video.id ? null : video.id)}><Icon name="more" size={15}/></button>{openMenu === video.id && <div className="menu-popover">
            <Link href={`/videos/${video.id}`}>Ouvrir</Link>
            {active && <button onClick={() => { setOpenMenu(null); if (running) setCancelTarget(video); else void cancel(video); }}><Icon name="close" size={14}/>Annuler le traitement</button>}
            {(video.status === "FAILED" || video.status === "CANCELLED") && <button onClick={() => retry(video)}><Icon name="retry" size={14}/>Relancer</button>}
            {video.status === "COMPLETED" && <a href={`${API}/videos/${video.id}/exports/summary.md`}><Icon name="download" size={14}/>Télécharger</a>}
            {!running && <button className="danger" onClick={() => { setDeleteTarget(video); setOpenMenu(null); }}><Icon name="trash" size={14}/>Supprimer</button>}
          </div>}</div>
        </div>;
      })}
    </div>
    {deleteTarget && <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="delete-title"><div className="modal"><h2 id="delete-title">Supprimer cette analyse ?</h2><p>Le fichier source, la transcription, le résumé et l’historique de chat associés seront supprimés définitivement.{isActive(deleteTarget.job) ? " Le traitement en attente est annulé." : ""}</p>{error && <div className="error">{error}</div>}<div className="modal-actions"><button className="btn" onClick={() => setDeleteTarget(null)} disabled={busy}>Annuler</button><button className="btn primary" style={{ background: "var(--danger)", borderColor: "var(--danger)" }} onClick={removeVideo} disabled={busy}>{busy ? "Suppression…" : "Supprimer"}</button></div></div></div>}
    {cancelTarget && <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="cancel-title"><div className="modal"><h2 id="cancel-title">Annuler ce traitement ?</h2><p>Le travail en cours sur « {cancelTarget.original_filename} » est abandonné. {cancelTarget.status === "COMPLETED" ? "Le résumé actuel est conservé." : "Vous pourrez relancer l’analyse plus tard."}</p>{error && <div className="error">{error}</div>}<div className="modal-actions"><button className="btn" onClick={() => setCancelTarget(null)} disabled={busy}>Continuer</button><button className="btn primary" style={{ background: "var(--danger)", borderColor: "var(--danger)" }} onClick={() => void cancel(cancelTarget)} disabled={busy}>{busy ? "Annulation…" : "Annuler le traitement"}</button></div></div></div>}
    {toast && <div className="toast">✓ {toast}</div>}
  </>;
}
