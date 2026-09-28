"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";

type Item = {
  error?: string; covered?: number; topics?: number; missing?: string[]; unsaid?: string[]; words?: number; budget?: number; length_ratio?: number | null;
  chapters?: number; chapter_hits?: number | null; chapter_expected?: number | null; language?: string | null; language_ok?: boolean | null;
  transcription_seconds?: number | null; summary_seconds?: number;
};
type Change = { key: string; measure: string; worse: boolean; text: string };
type Run = {
  id: string; status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED"; scope: "quick" | "full"; trigger: "manual" | "auto";
  llm_model: string; whisper_model: string; prompt_version: string; progress: number; current: string | null; score: number | null;
  error: string | null; created_at: string; finished_at: string | null; previous_id: string | null; previous_score: number | null;
  changes: Change[]; items: Record<string, Item>;
};
type CorpusItem = { key: string; title: string; duration_seconds: number | null; present: boolean; topics: number; scopes: string[] };
type Overview = {
  items: CorpusItem[]; available: boolean; settings: { auto: boolean };
  current: { llm_model: string; whisper_model: string; prompt_version: string; tested: boolean }; runs: Run[];
};

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const statusLabels: Record<Run["status"], string> = { QUEUED: "en attente", RUNNING: "en cours", COMPLETED: "terminée", FAILED: "échec", CANCELLED: "annulée" };
const percent = (value: number | null | undefined) => value == null ? "—" : `${Math.round(100 * value)} %`;
const when = (value: string) => new Date(value).toLocaleString("fr-FR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

function ScoreDelta({ run }: { run: Run }) {
  if (run.score == null || run.previous_score == null) return null;
  const delta = Math.round(10 * (run.score - run.previous_score)) / 10;
  if (!delta) return <span className="field-hint">= précédente</span>;
  return <span className={delta < 0 ? "quality-worse" : "quality-better"}>{delta > 0 ? "+" : ""}{String(delta).replace(".", ",")} pt</span>;
}

/** n°4: the reference corpus replayed after each change of prompt or model, compared with the run before. */
export default function QualityPanel() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setOverview(await api<Overview>("/quality")); setError(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }, []);
  const active = overview?.runs.some(run => run.status === "QUEUED" || run.status === "RUNNING");
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(timer);
  }, [active, load]);

  async function start(scope: "quick" | "full") {
    setBusy(true); setError("");
    try { await api("/quality/runs", json("POST", { scope })); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  async function cancel(id: string) {
    try { await api(`/quality/runs/${id}/cancel`, { method: "POST" }); await load(); }
    catch (reason) { setError(String(reason)); }
  }

  async function setAuto(auto: boolean) {
    try { await api("/settings/quality", json("PUT", { auto })); await load(); }
    catch (reason) { setError(String(reason)); }
  }

  if (!overview) return error ? <section className="card settings-section"><h2>Suivi de qualité</h2><div className="error">{error}</div></section> : null;
  const quick = overview.items.filter(item => item.scopes.includes("quick"));
  if (!overview.available && !overview.runs.length) {
    return <section className="card settings-section" id="qualite">
      <h2>Suivi de qualité</h2>
      <p className="muted">Le corpus de référence (enregistrements sous licence libre et sujets attendus dans leurs résumés) n&apos;est pas sur cet ordinateur : placez-le dans <code>data/corpus</code> (voir son README) pour que chaque changement de modèle ou de prompt soit mesuré.</p>
    </section>;
  }

  return <section className="card settings-section" id="qualite">
    <div className="spread">
      <h2>Suivi de qualité</h2>
      <div className="row">
        <button type="button" className="btn small" onClick={() => void start("quick")} disabled={busy || !!active || !overview.available}>Évaluation rapide</button>
        <button type="button" className="btn small" onClick={() => void start("full")} disabled={busy || !!active || !overview.available} title="Ajoute un cours et quatre conférences : environ 5 heures d'enregistrement">Complète</button>
      </div>
    </div>
    <p className="muted">Le corpus de référence est rejoué avec le code des analyses : couverture des sujets attendus (tirés des descriptions officielles, jamais de nos résumés), longueur par rapport au budget de mots, débuts de chapitres retrouvés. Rapide : {quick.map(item => item.key).join(", ")}. Les transcriptions sont gardées en cache par modèle Whisper : seul un changement de Whisper les refait.</p>
    <p className="field-hint">Configuration actuelle : {overview.current.llm_model} · {overview.current.whisper_model} · prompts <span className="mono">{overview.current.prompt_version}</span> — {overview.current.tested ? "déjà évaluée" : <strong>pas encore évaluée</strong>}</p>
    <label className="checkbox"><input type="checkbox" checked={overview.settings.auto} onChange={event => void setAuto(event.target.checked)} /><span>Évaluer automatiquement (rapide) quand un modèle ou un prompt change</span></label>
    {error && <div className="error" role="alert">{error}</div>}
    {!!overview.runs.length && <table className="quality-table">
      <thead><tr><th>Date</th><th>Modèles</th><th>Prompts</th><th>Couverture</th><th>Évolution</th><th /></tr></thead>
      <tbody>{overview.runs.map(run => {
        const running = run.status === "QUEUED" || run.status === "RUNNING";
        const worse = run.changes.filter(change => change.worse);
        return <Fragment key={run.id}>
          <tr className={open === run.id ? "open" : undefined}>
            <td>{when(run.created_at)}<span className="field-hint"> · {run.scope === "quick" ? "rapide" : "complète"}{run.trigger === "auto" ? " · auto" : ""}</span></td>
            <td className="mono">{run.llm_model}<br />{run.whisper_model}</td>
            <td className="mono">{run.prompt_version}</td>
            <td>{run.status === "COMPLETED" ? <strong>{run.score == null ? "—" : `${String(run.score).replace(".", ",")} %`}</strong> : running
              ? <><div className="progress-track small" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={run.progress}><div style={{ width: `${run.progress}%` }} /></div><span className="field-hint">{run.current || statusLabels[run.status]}</span></>
              : <span className={run.status === "FAILED" ? "error inline-error" : "field-hint"} title={run.error || undefined}>{statusLabels[run.status]}{run.error ? ` : ${run.error}` : ""}</span>}</td>
            <td><ScoreDelta run={run} />{!!worse.length && <span className="quality-worse"> · {worse.length} recul{worse.length > 1 ? "s" : ""}</span>}</td>
            <td className="row">
              {!!Object.keys(run.items).length && <button type="button" className="btn small" onClick={() => setOpen(current => current === run.id ? null : run.id)}>{open === run.id ? "Masquer" : "Détail"}</button>}
              {running && <button type="button" className="btn small" onClick={() => void cancel(run.id)}>Annuler</button>}
            </td>
          </tr>
          {open === run.id && <tr className="quality-detail"><td colSpan={6}>
            {!!run.changes.length && <ul className="quality-changes">{run.changes.map(change => <li key={`${change.key}-${change.measure}`} className={change.worse ? "quality-worse" : "quality-better"}>{change.worse ? "▼" : "▲"} {change.key} : {change.text}</li>)}</ul>}
            <table className="quality-items"><thead><tr><th>Enregistrement</th><th>Sujets couverts</th><th>Longueur / budget</th><th>Chapitres</th><th>Langue</th><th>Temps</th></tr></thead>
              <tbody>{Object.entries(run.items).map(([key, item]) => item.error
                ? <tr key={key}><td>{key}</td><td colSpan={5} className="field-hint">{item.error}</td></tr>
                : <tr key={key}>
                  <td>{key}</td>
                  <td>{item.covered}/{item.topics}{!!item.missing?.length && <span className="field-hint"> · manque : {item.missing.join(", ")}</span>}{!!item.unsaid?.length && <span className="field-hint"> · jamais dit dans l&apos;enregistrement, non compté : {item.unsaid.join(", ")}</span>}</td>
                  <td>{item.words} / {item.budget} mots ({percent(item.length_ratio)})</td>
                  <td>{item.chapters}{item.chapter_expected ? ` · ${item.chapter_hits}/${item.chapter_expected} débuts attendus` : ""}</td>
                  <td>{item.language ?? "?"}{item.language_ok === false ? " ✕" : ""}</td>
                  <td className="field-hint">{item.transcription_seconds != null ? `${item.transcription_seconds} s transcription · ` : ""}{item.summary_seconds} s résumé</td>
                </tr>)}</tbody></table>
          </td></tr>}
        </Fragment>;
      })}</tbody>
    </table>}
  </section>;
}
