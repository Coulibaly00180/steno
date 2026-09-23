"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, formatBytes } from "../lib/api";

type StorageRow = {
  id: string; original_filename: string; status: string; source_policy: string; busy: boolean;
  source_bytes: number; audio_bytes: number; exports_bytes: number; total_bytes: number;
  source_available: boolean; source_kind: "audio" | "video"; can_compact: boolean;
};
type StorageReport = {
  disk_total_bytes: number | null; disk_free_bytes: number | null; backups_bytes: number; inbox_bytes: number;
  totals: { source_bytes: number; audio_bytes: number; exports_bytes: number; total_bytes: number };
  videos: StorageRow[];
};
type Action = "audio" | "delete_media" | "delete_work_audio";

const SHOWN = 15;
const POLL_MS = 5000;
const size = (bytes: number) => bytes ? formatBytes(bytes) : "—";

const confirmations: Record<Action, { title: string; body: string; button: string }> = {
  audio: { title: "Garder seulement l'audio ?", body: "Le fichier est remplacé par une piste audio compacte (~20 Mo par heure). La lecture, les sous-titres et les exports restent disponibles ; l'image d'une vidéo est perdue définitivement.", button: "Convertir" },
  delete_media: { title: "Supprimer les médias ?", body: "Le fichier source et la piste de travail sont supprimés définitivement. La transcription, la traduction, le résumé et les exports sont conservés, mais la vidéo ne pourra plus être lue ni ses intervenants identifiés.", button: "Supprimer les médias" },
  delete_work_audio: { title: "Supprimer la piste de travail ?", body: "Le fichier WAV extrait pour la transcription est supprimé. Il sera recréé à partir de la source si besoin (identification des intervenants).", button: "Supprimer" },
};

export default function StoragePanel() {
  const [report, setReport] = useState<StorageReport | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [pending, setPending] = useState<{ row: StorageRow; action: Action } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try { setReport(await api<StorageReport>("/storage")); }
    catch (reason) { setError(String(reason)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  // A conversion runs in the worker: follow it until it ends.
  const converting = !!report?.videos.some(row => row.busy);
  useEffect(() => {
    if (!converting) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [converting, load]);

  async function run() {
    if (!pending) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await api<{ job: unknown; freed_bytes: number }>(`/videos/${pending.row.id}/storage`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: pending.action }) });
      setMessage(result.job ? `Conversion de « ${pending.row.original_filename} » ajoutée à la file.` : `${formatBytes(result.freed_bytes)} libérés.`);
      setPending(null);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setPending(null); }
    finally { setBusy(false); }
  }

  const rows = report ? (showAll ? report.videos : report.videos.slice(0, SHOWN)) : [];
  const used = report?.disk_total_bytes && report.disk_free_bytes != null ? report.disk_total_bytes - report.disk_free_bytes : null;
  const usedPercent = used != null && report?.disk_total_bytes ? Math.round((100 * used) / report.disk_total_bytes) : null;

  return <section className="card settings-section" id="stockage">
    <div className="spread"><h2>Espace disque</h2><button type="button" className="btn small" onClick={() => void load()}>Actualiser</button></div>
    <p className="muted">Ce qu&apos;occupe chaque vidéo : le fichier importé, la piste de travail (WAV extrait pour la transcription, ~115 Mo par heure) et les exports. Libérer les médias ne touche jamais au texte.</p>
    {report && <>
      {usedPercent != null && <div className="disk-usage"><div className="progress-track small" role="progressbar" aria-label="Occupation du disque" aria-valuemin={0} aria-valuemax={100} aria-valuenow={usedPercent}><div style={{ width: `${usedPercent}%` }} /></div><span className="field-hint">Disque : {formatBytes(report.disk_free_bytes!)} libres sur {formatBytes(report.disk_total_bytes!)} ({usedPercent} % occupés)</span></div>}
      <div className="storage-totals">
        <div><span className="field-label">Fichiers importés</span><strong>{size(report.totals.source_bytes)}</strong></div>
        <div><span className="field-label">Pistes de travail</span><strong>{size(report.totals.audio_bytes)}</strong></div>
        <div><span className="field-label">Exports</span><strong>{size(report.totals.exports_bytes)}</strong></div>
        <div><span className="field-label">Sauvegardes</span><strong>{size(report.backups_bytes)}</strong></div>
      </div>
      {!report.videos.length ? <p className="muted">Aucune vidéo.</p> : <div className="storage-table" role="table" aria-label="Espace occupé par vidéo">
        <div className="storage-row storage-head" role="row"><span role="columnheader">Vidéo</span><span role="columnheader">Source</span><span role="columnheader">Piste de travail</span><span role="columnheader">Total</span><span role="columnheader" /></div>
        {rows.map(row => <div className="storage-row" role="row" key={row.id}>
          <span role="cell" className="storage-name"><Link href={`/videos/${row.id}`}>{row.original_filename}</Link>{!row.source_available && !row.audio_bytes && <span className="field-hint">médias supprimés</span>}{row.source_available && row.source_kind === "audio" && <span className="field-hint">audio</span>}</span>
          <span role="cell" className="mono">{size(row.source_bytes)}</span>
          <span role="cell" className="mono">{size(row.audio_bytes)}</span>
          <span role="cell" className="mono"><strong>{size(row.total_bytes)}</strong></span>
          <span role="cell" className="row storage-actions">{row.busy ? <span className="field-hint">traitement en cours…</span> : row.status !== "COMPLETED" ? <span className="field-hint">non traitée</span> : <>
            {row.can_compact && <button type="button" className="btn small" onClick={() => setPending({ row, action: "audio" })}>{row.source_kind === "video" ? "Garder l'audio" : "Compresser l'audio"}</button>}
            {row.audio_bytes > 0 && row.source_available && <button type="button" className="btn small" onClick={() => setPending({ row, action: "delete_work_audio" })}>Suppr. piste de travail</button>}
            {(row.source_available || row.audio_bytes > 0) && <button type="button" className="btn small danger" onClick={() => setPending({ row, action: "delete_media" })}>Suppr. médias</button>}
          </>}</span>
        </div>)}
      </div>}
      {report.videos.length > SHOWN && <button type="button" className="btn small storage-more" onClick={() => setShowAll(value => !value)}>{showAll ? "Afficher les plus volumineuses" : `Afficher les ${report.videos.length} vidéos`}</button>}
    </>}
    {message && <p className="success-note" role="status">{message}</p>}
    {error && <div className="error" role="alert">{error}</div>}
    {pending && <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="storage-title"><div className="modal"><h2 id="storage-title">{confirmations[pending.action].title}</h2><p><strong>{pending.row.original_filename}</strong></p><p>{confirmations[pending.action].body}</p><div className="modal-actions"><button className="btn" onClick={() => setPending(null)} disabled={busy}>Annuler</button><button className="btn primary" style={pending.action === "delete_media" ? { background: "var(--danger)", borderColor: "var(--danger)" } : undefined} onClick={() => void run()} disabled={busy}>{busy ? "…" : confirmations[pending.action].button}</button></div></div></div>}
  </section>;
}
