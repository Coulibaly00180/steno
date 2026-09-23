"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API, api, formatBytes, uploadForm } from "../lib/api";

type Backup = { name: string; kind: "auto" | "manuel" | "avant-restauration"; size_bytes: number; created_at: string };
type BackupSettings = { enabled: boolean; interval_hours: number; keep: number };
type BackupState = {
  settings: BackupSettings; folder: string; backups: Backup[];
  last_error: string | null; last_error_at: string | null; last_success_at: string | null;
};
type ImportReport = {
  videos_added: number; videos_skipped: number; media_added: number; templates_added: number;
  glossary_added: number; conversations_added: number; errors: string[];
};

const kindLabels: Record<Backup["kind"], string> = { auto: "automatique", manuel: "manuelle", "avant-restauration": "avant restauration" };
const intervals = [{ value: 6, label: "toutes les 6 heures" }, { value: 12, label: "toutes les 12 heures" }, { value: 24, label: "chaque jour" }, { value: 72, label: "tous les 3 jours" }, { value: 168, label: "chaque semaine" }];
const dateTime = (value: string) => new Date(value).toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" });

function Command({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try { await navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 1500); } catch { /* the command stays selectable */ }
  }
  return <div className="command"><code>{text}</code><button type="button" className="btn small" onClick={() => void copy()}>{copied ? "Copié" : "Copier"}</button></div>;
}

export default function BackupsPanel() {
  const [state, setState] = useState<BackupState | null>(null);
  const [config, setConfig] = useState<BackupSettings | null>(null);
  const [busy, setBusy] = useState<"" | "backup" | "settings" | "import">("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [restoreTarget, setRestoreTarget] = useState<Backup | null>(null);
  const [withMedia, setWithMedia] = useState(false);
  const [importProgress, setImportProgress] = useState<number | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try { const value = await api<BackupState>("/backups"); setState(value); setConfig(current => current ?? value.settings); }
    catch (reason) { setError(String(reason)); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const failure = (reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason));
  const dirty = !!state && !!config && JSON.stringify(config) !== JSON.stringify(state.settings);

  async function backupNow() {
    setBusy("backup"); setError(""); setMessage("");
    try { const created = await api<Backup>("/backups", { method: "POST" }); setMessage(`Sauvegarde ${created.name} écrite (${formatBytes(created.size_bytes)}).`); await load(); }
    catch (reason) { failure(reason); }
    finally { setBusy(""); }
  }

  async function saveSettings() {
    if (!config) return;
    setBusy("settings"); setError(""); setMessage("");
    try { const value = await api<BackupSettings>("/settings/backups", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config) }); setConfig(value); setMessage("Planification enregistrée."); await load(); }
    catch (reason) { failure(reason); }
    finally { setBusy(""); }
  }

  async function remove(backup: Backup) {
    if (!window.confirm(`Supprimer définitivement la sauvegarde du ${dateTime(backup.created_at)} ?`)) return;
    setError("");
    try { await api(`/backups/${encodeURIComponent(backup.name)}`, { method: "DELETE" }); await load(); }
    catch (reason) { failure(reason); }
  }

  async function importArchive(file: File | undefined) {
    if (!file) return;
    setBusy("import"); setError(""); setMessage(""); setReport(null); setImportProgress(0);
    const form = new FormData(); form.append("file", file);
    try {
      setReport(await uploadForm<ImportReport>("/library/import", form, (loaded, total) => setImportProgress(total ? Math.floor((100 * loaded) / total) : null)));
    } catch (reason) { failure(reason); }
    finally { setBusy(""); setImportProgress(null); if (fileInput.current) fileInput.current.value = ""; }
  }

  const latest = state?.backups[0];
  return <section className="card settings-section" id="sauvegardes">
    <h2>Sauvegardes</h2>
    <p className="muted">Une copie complète de la base (transcriptions, résumés, conversations, réglages) est écrite dans <code>{state?.folder ?? "data/backups"}</code>. Les médias sont dans <code>data/</code> : pour tout protéger, copiez régulièrement le dossier <code>data</code> sur un autre disque.</p>
    {state?.last_error && <div className="status-banner" role="status"><p>Dernière sauvegarde automatique en échec{state.last_error_at ? ` (${dateTime(state.last_error_at)})` : ""} : {state.last_error}</p></div>}
    {config && <div className="backup-settings">
      <label className="checkbox"><input type="checkbox" checked={config.enabled} onChange={event => setConfig({ ...config, enabled: event.target.checked })} /><span>Sauvegarde automatique</span></label>
      <select aria-label="Fréquence des sauvegardes" value={config.interval_hours} onChange={event => setConfig({ ...config, interval_hours: Number(event.target.value) })} disabled={!config.enabled}>{intervals.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}{!intervals.some(option => option.value === config.interval_hours) && <option value={config.interval_hours}>toutes les {config.interval_hours} heures</option>}</select>
      <label className="keep-count">Conserver les <input type="number" min={1} max={100} value={config.keep} onChange={event => setConfig({ ...config, keep: Number(event.target.value) || 1 })} disabled={!config.enabled} /> dernières</label>
      <button type="button" className="btn" onClick={() => void saveSettings()} disabled={!dirty || busy !== ""}>{busy === "settings" ? "…" : "Enregistrer"}</button>
    </div>}
    <div className="row backup-actions"><button type="button" className="btn primary" onClick={() => void backupNow()} disabled={busy !== ""}>{busy === "backup" ? "Sauvegarde en cours…" : "Sauvegarder maintenant"}</button>{latest && <span className="field-hint">Dernière : {dateTime(latest.created_at)}</span>}</div>
    {!!state?.backups.length && <ul className="backup-list">{state.backups.map(backup => <li key={backup.name}>
      <span><strong>{dateTime(backup.created_at)}</strong> <span className="pill">{kindLabels[backup.kind]}</span></span>
      <span className="mono field-hint">{formatBytes(backup.size_bytes)}</span>
      <span className="row"><a className="btn small" href={`${API}/backups/${encodeURIComponent(backup.name)}`}>Télécharger</a><button type="button" className="btn small" onClick={() => setRestoreTarget(backup)}>Restaurer…</button><button type="button" className="btn small danger" onClick={() => void remove(backup)}>Supprimer</button></span>
    </li>)}</ul>}
    {state && !state.backups.length && <p className="muted">Aucune sauvegarde pour le moment.</p>}

    <h3 className="subsection-title">Archive de la bibliothèque</h3>
    <p className="muted">Pour déplacer vos analyses vers une autre installation de Sténo, ou y ajouter celles d&apos;un collègue. L&apos;import ajoute les vidéos absentes et ne remplace rien ; l&apos;index des questions est reconstruit ensuite.</p>
    <div className="row archive-actions">
      <label className="checkbox"><input type="checkbox" checked={withMedia} onChange={event => setWithMedia(event.target.checked)} /><span>Inclure les médias <span className="muted">(archive bien plus lourde)</span></span></label>
      <a className="btn" href={`${API}/library/export${withMedia ? "?media=true" : ""}`}>Exporter la bibliothèque</a>
      <input ref={fileInput} type="file" hidden accept=".tar,application/x-tar" onChange={event => void importArchive(event.target.files?.[0])} />
      <button type="button" className="btn" onClick={() => fileInput.current?.click()} disabled={busy !== ""}>{busy === "import" ? (importProgress != null && importProgress < 100 ? `Envoi… ${importProgress} %` : "Import en cours…") : "Importer une archive"}</button>
    </div>
    {report && <div className="status-banner success-banner" role="status"><p>{report.videos_added} vidéo{report.videos_added > 1 ? "s" : ""} ajoutée{report.videos_added > 1 ? "s" : ""}{report.media_added ? ` (dont ${report.media_added} avec leurs médias)` : ""}{report.videos_skipped ? ` · ${report.videos_skipped} déjà présente${report.videos_skipped > 1 ? "s" : ""}` : ""}{report.templates_added ? ` · ${report.templates_added} template(s)` : ""}{report.glossary_added ? ` · ${report.glossary_added} terme(s) de glossaire` : ""}{report.conversations_added ? ` · ${report.conversations_added} conversation(s)` : ""}.</p>{report.errors.map(item => <p key={item}>{item}</p>)}</div>}
    {message && <p className="success-note" role="status">{message}</p>}
    {error && <div className="error" role="alert">{error}</div>}

    {restoreTarget && <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="restore-title"><div className="modal wide-modal">
      <h2 id="restore-title">Restaurer la sauvegarde du {dateTime(restoreTarget.created_at)}</h2>
      <p>La base actuelle est <strong>remplacée</strong> : les analyses faites depuis cette date disparaissent de la bibliothèque (leurs fichiers restent dans <code>data/</code>). Une copie de sécurité de la base actuelle est faite juste avant. La restauration se lance depuis un terminal, dans le dossier du projet, application arrêtée :</p>
      <ol className="restore-steps">
        <li>Arrêter les services qui utilisent la base :<Command text="docker compose stop api worker scheduler web" /></li>
        <li>Restaurer (dans une base temporaire, qui ne remplace l&apos;actuelle qu&apos;en cas de succès) :<Command text={`docker compose --profile tools run --rm restore ${restoreTarget.name}`} /></li>
        <li>Relancer l&apos;application (les migrations mettent la base à jour si besoin) :<Command text="docker compose -f compose.yaml -f compose.gpu.yaml up -d" /><span className="field-hint">Sans carte NVIDIA : <code>docker compose up -d</code></span></li>
      </ol>
      <div className="modal-actions"><button className="btn" onClick={() => setRestoreTarget(null)}>Fermer</button></div>
    </div></div>}
  </section>;
}
