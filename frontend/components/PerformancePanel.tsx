"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { stageLabels } from "../lib/jobs";

type Stage = { stage: string; jobs: number; per_media_hour: number | null; recent: number | null; earlier: number | null };
type Report = {
  kind: string; jobs: number; media_hours: number; total_per_media_hour: number | null; total_recent: number | null;
  total_earlier: number | null; queued_median_seconds: number | null; stages: Stage[];
};

const kinds = [{ value: "FULL", label: "Analyses" }, { value: "SUMMARY", label: "Nouveaux résumés" }, { value: "ENTITIES", label: "Personnes et dates" }, { value: "INDEX", label: "Indexation" }];

function seconds(value: number | null) {
  if (value == null) return "—";
  if (value < 60) return `${Math.round(value)} s`;
  const minutes = Math.floor(value / 60), rest = Math.round(value % 60);
  return rest ? `${minutes} min ${String(rest).padStart(2, "0")}` : `${minutes} min`;
}

/** Recent against earlier: a change of more than 15 % is worth a look (model, prompt, driver, a game on the card…). */
function Trend({ recent, earlier }: { recent: number | null; earlier: number | null }) {
  if (recent == null || earlier == null || !earlier) return <span className="field-hint">—</span>;
  const change = Math.round(100 * (recent - earlier) / earlier);
  if (Math.abs(change) < 15) return <span className="field-hint">stable</span>;
  return <span className={change > 0 ? "quality-worse" : "quality-better"}>{change > 0 ? "+" : ""}{change} %</span>;
}

/** Time per stage over the latest jobs, measured on this computer. */
export default function PerformancePanel() {
  const [kind, setKind] = useState("FULL");
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Report>(`/performance?kind=${kind}`).then(result => { setReport(result); setError(""); }).catch(reason => setError(String(reason)));
  }, [kind]);

  const shown = report?.stages.filter(stage => stage.per_media_hour != null && stage.per_media_hour >= 0.5) ?? [];
  const total = report?.total_per_media_hour ?? null;
  return <section className="card settings-section" id="performances">
    <div className="spread">
      <h2>Temps de traitement</h2>
      <div className="segmented" role="radiogroup" aria-label="Type de traitement">{kinds.map(option => <button type="button" role="radio" aria-checked={kind === option.value} className={kind === option.value ? "selected" : ""} key={option.value} onClick={() => setKind(option.value)}>{option.label}</button>)}</div>
    </div>
    <p className="muted">Mesuré sur cet ordinateur à chaque traitement : médiane par étape, ramenée à une heure de média, {report ? (report.jobs > 1 ? `sur les ${report.jobs} derniers traitements` : `sur ${report.jobs} traitement`) : "…"}. « Récents » compare les 10 derniers aux précédents.</p>
    {error && <div className="error">{error}</div>}
    {report && report.jobs < 3 && <p className="field-hint">Pas encore assez de traitements mesurés (au moins 3).</p>}
    {report && report.jobs >= 3 && <>
      <table className="quality-items performance-table">
        <thead><tr><th>Étape</th><th>Pour 1 h de média</th><th>Part</th><th>Récents</th></tr></thead>
        <tbody>
          {shown.map(stage => <tr key={stage.stage}>
            <td>{stageLabels[stage.stage] || stage.stage}</td>
            <td className="mono">{seconds(stage.per_media_hour)}</td>
            <td><span className="share-bar" style={{ width: `${total ? Math.min(100, Math.round(100 * (stage.per_media_hour ?? 0) / total)) : 0}%` }} /></td>
            <td><Trend recent={stage.recent} earlier={stage.earlier} /></td>
          </tr>)}
          <tr className="performance-total"><td>Total</td><td className="mono">{seconds(total)}</td><td /><td><Trend recent={report.total_recent} earlier={report.total_earlier} /></td></tr>
        </tbody>
      </table>
      <p className="field-hint">{report.media_hours.toString().replace(".", ",")} h de média mesurées{report.queued_median_seconds != null ? ` · attente médiane dans la file : ${seconds(report.queued_median_seconds)}` : ""}. Les étapes sous une demi-seconde par heure ne sont pas affichées.</p>
    </>}
  </section>;
}
