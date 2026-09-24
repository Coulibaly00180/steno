"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, formatBytes } from "../../lib/api";
import { pullModel, type Pull } from "../../lib/models";
import { serviceLabels, type ServiceName, type SystemStatus } from "../../lib/status";
import { Icon } from "../../components/Icons";

type Models = {
  llm: { current: string }; whisper: { current: string; device: string; choices: { name: string; cached: boolean }[] }; embedding_model: string;
  installed: { name: string; embedding: boolean }[]; gpu: { name?: string; memory_total_mb?: number } | null; ollama_error: string | null;
};

const same = (a: string, b: string) => (a.includes(":") ? a : `${a}:latest`) === (b.includes(":") ? b : `${b}:latest`);
const steps = ["Vérification", "Modèles", "Accès", "Premier import"];

async function finish() {
  await api("/settings/onboarding", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ done: true }) });
}

/** n°19: the first launch, after the Windows installer: check, models, access, first import. */
export default function WelcomePage() {
  const [step, setStep] = useState(0);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [models, setModels] = useState<Models | null>(null);
  const [pulls, setPulls] = useState<Record<string, Pull>>({});
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [system, overview] = await Promise.all([api<SystemStatus>("/status"), api<Models>("/models")]);
      setStatus(system); setModels(overview); setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }, []);
  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 8000); return () => window.clearInterval(timer); }, [load]);

  async function download(name: string) {
    setPulls(current => ({ ...current, [name]: { status: "Préparation…" } }));
    try {
      await pullModel(name, pull => setPulls(current => ({ ...current, [name]: pull })));
      setPulls(current => { const next = { ...current }; delete next[name]; return next; });
      await load();
    } catch (reason) {
      setPulls(current => ({ ...current, [name]: { status: "échec", error: reason instanceof Error ? reason.message : String(reason) } }));
    }
  }

  const needed = models ? [
    { name: models.llm.current, role: "résumés, traduction et chat" },
    { name: models.embedding_model, role: "recherche par le sens" },
  ].map(item => ({ ...item, installed: models.installed.some(model => same(model.name, item.name)) })) : [];
  const servicesOk = !!status && (Object.keys(serviceLabels) as ServiceName[]).every(name => ["ok", "degraded"].includes(status.services[name]?.status ?? ""));

  return <div className="page welcome">
    <header className="page-header"><h1>Bienvenue dans Sténo</h1><p>Quelques vérifications avant votre première vidéo. Tout reste sur cet ordinateur : aucune donnée n&apos;est envoyée à un service en ligne.</p></header>
    <ol className="welcome-steps">{steps.map((label, index) => <li key={label} className={index === step ? "current" : index < step ? "done" : undefined}><button type="button" onClick={() => setStep(index)}>{index < step ? "✓" : index + 1}. {label}</button></li>)}</ol>
    {error && <div className="error" role="alert">{error}</div>}

    {step === 0 && <section className="card settings-section">
      <h2>Les services</h2>
      {!status ? <p className="muted">Vérification…</p> : <ul className="welcome-checks">{(Object.keys(serviceLabels) as ServiceName[]).map(name => {
        const service = status.services[name];
        return <li key={name} className={service?.status === "ok" ? "service-ok" : service?.status === "degraded" ? "service-warn" : "service-bad"}><i /><span>{serviceLabels[name]}</span><span className="field-hint">{service?.detail || service?.status}</span></li>;
      })}</ul>}
      <h2>La carte graphique</h2>
      {models?.gpu?.name ? <p><strong>{models.gpu.name}</strong>{models.gpu.memory_total_mb ? ` · ${(models.gpu.memory_total_mb / 1024).toFixed(0)} Go de mémoire vidéo` : ""} : la transcription et les résumés tournent dessus.</p>
        : <p className="muted">Pas de carte graphique utilisée : tout tourne sur le processeur. Comptez environ le temps réel pour transcrire (une heure de réunion, une heure de traitement) avec le modèle « small ».</p>}
      <div className="row"><button type="button" className="btn primary" onClick={() => setStep(1)}>Continuer</button>{!servicesOk && status && <span className="field-hint">Un service ne répond pas encore : il peut démarrer encore quelques secondes.</span>}</div>
    </section>}

    {step === 1 && <section className="card settings-section">
      <h2>Les modèles d&apos;IA</h2>
      <p className="muted">Ils sont téléchargés une fois (quelques Go), puis tout fonctionne hors connexion.</p>
      <ul className="model-list compact">{needed.map(item => {
        const pull = pulls[item.name];
        const percent = pull?.total ? Math.floor(100 * (pull.completed ?? 0) / pull.total) : 0;
        return <li key={item.name}>
          <div className="model-name"><strong>{item.name}</strong><span className="field-hint">{item.role}</span></div>
          {item.installed ? <span className="service-ok"><i />installé</span> : pull ? (pull.error ? <span className="error inline-error">{pull.error}</span> : <div className="pull-progress"><div className="progress-track small" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><div style={{ width: `${percent}%` }} /></div><span className="field-hint">{pull.status}{pull.total ? ` · ${formatBytes(pull.completed ?? 0)} / ${formatBytes(pull.total)}` : ""}</span></div>)
            : <button type="button" className="btn small primary" onClick={() => void download(item.name)} disabled={!!models?.ollama_error}>Télécharger</button>}
        </li>;
      })}
        {models && <li><div className="model-name"><strong>{models.whisper.current}</strong><span className="field-hint">transcription, sur {models.whisper.device === "cuda" ? "la carte graphique" : "le processeur"}</span></div>{models.whisper.choices.some(choice => choice.name === models.whisper.current && choice.cached) ? <span className="service-ok"><i />installé</span> : <span className="field-hint">téléchargé à la première analyse</span>}</li>}
      </ul>
      {models?.ollama_error && <div className="status-banner" role="status"><p>{models.ollama_error}</p></div>}
      <p className="field-hint">D&apos;autres modèles, plus légers ou plus fins, se choisissent ensuite dans la page <Link className="text-link" href="/models">Modèles</Link>.</p>
      <div className="row"><button type="button" className="btn primary" onClick={() => setStep(2)} disabled={needed.some(item => !item.installed)}>Continuer</button>{needed.some(item => !item.installed) && <span className="field-hint">Téléchargez d&apos;abord les modèles manquants.</span>}</div>
    </section>}

    {step === 2 && <section className="card settings-section">
      <h2>Accès depuis d&apos;autres appareils (facultatif)</h2>
      <p className="muted">Par défaut, Sténo ne s&apos;ouvre que sur cet ordinateur. Pour l&apos;utiliser depuis un téléphone ou un autre ordinateur de la maison ou du bureau, définissez un mot de passe et démarrez l&apos;accès HTTPS : tout est expliqué dans Paramètres › Accès et sécurité.</p>
      <div className="row"><Link className="btn" href="/settings#acces"><Icon name="lock" size={14} />Régler l&apos;accès</Link><button type="button" className="btn primary" onClick={() => setStep(3)}>Plus tard</button></div>
    </section>}

    {step === 3 && <section className="card settings-section">
      <h2>C&apos;est prêt</h2>
      <p className="muted">Importez une vidéo ou un fichier audio (jusqu&apos;à 6 heures), enregistrez une réunion depuis le navigateur, ou déposez des fichiers dans le dossier <code>data/inbox</code>.</p>
      <div className="row">
        <button type="button" className="btn primary" onClick={() => void finish().then(() => window.location.assign("/"))}><Icon name="upload" size={14} />Importer une vidéo</button>
        <button type="button" className="btn" onClick={() => void finish().then(() => window.location.assign("/record"))}><Icon name="mic" size={14} />Enregistrer une réunion</button>
      </div>
    </section>}

    <p className="welcome-skip"><button type="button" className="link-button" onClick={() => void finish().then(() => window.location.assign("/"))}>Passer l&apos;assistant</button></p>
  </div>;
}
