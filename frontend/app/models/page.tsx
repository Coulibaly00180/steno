"use client";

import { useCallback, useEffect, useState } from "react";
import { api, formatBytes } from "../../lib/api";
import { pullModel, type Pull } from "../../lib/models";
import QualityPanel from "../../components/QualityPanel";

type Installed = { name: string; size_bytes: number | null; parameter_size: string | null; quantization: string | null; family: string | null; embedding: boolean };
type Suggestion = { name: string; vram: string; note: string; installed: boolean };
type WhisperChoice = { name: string; size: string; note: string; cached: boolean };
type Gpu = { name?: string; memory_total_mb?: number; memory_used_mb?: number; utilization?: number; whisper_device?: string } | null;
type Overview = {
  llm: { current: string; default: string; chosen: boolean };
  whisper: { current: string; default: string; chosen: boolean; live: string; device: string; choices: WhisperChoice[] };
  embedding_model: string; installed: Installed[]; suggestions: Suggestion[];
  loaded: { name: string; size_bytes: number; vram_bytes: number }[]; gpu: Gpu; ollama_error: string | null;
};
type LlmResult = { load_seconds: number; tokens_per_second: number | null; prompt_tokens_per_second: number | null; total_seconds: number; answer: string };
type WhisperResult = { status: "pending" | "running" | "done" | "error"; detail?: string; speed?: number; load_seconds?: number; audio_seconds?: number; transcribe_seconds?: number; sample?: string; text?: string; device?: string };

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const same = (a: string, b: string) => (a.includes(":") ? a : `${a}:latest`) === (b.includes(":") ? b : `${b}:latest`);

export default function ModelsPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  const [llmResults, setLlmResults] = useState<Record<string, LlmResult>>({});
  const [whisperResults, setWhisperResults] = useState<Record<string, WhisperResult>>({});
  const [pulls, setPulls] = useState<Record<string, Pull>>({});
  const [customName, setCustomName] = useState("");

  const load = useCallback(async () => {
    try { setOverview(await api<Overview>("/models")); setError(""); }
    catch (reason) { setError(String(reason)); }
  }, []);
  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 15000); return () => window.clearInterval(timer); }, [load]);

  const failure = (reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason));

  async function choose(change: { llm_model?: string | null; whisper_model?: string | null }) {
    if (!overview) return;
    setBusy("choose"); setError(""); setMessage("");
    const body = {
      llm_model: "llm_model" in change ? change.llm_model : overview.llm.chosen ? overview.llm.current : null,
      whisper_model: "whisper_model" in change ? change.whisper_model : overview.whisper.chosen ? overview.whisper.current : null,
    };
    try {
      const saved = await api<{ llm_model: string; whisper_model: string }>("/settings/models", json("PUT", body));
      setMessage(`Modèles utilisés : ${saved.llm_model} pour les résumés et le chat, ${saved.whisper_model} pour la transcription. Ils s'appliquent aux prochains traitements.`);
      await load();
    } catch (reason) { failure(reason); }
    finally { setBusy(""); }
  }

  async function testLlm(name: string) {
    setBusy(`llm:${name}`); setError("");
    try { const result = await api<LlmResult>("/models/llm/benchmark", json("POST", { name })); setLlmResults(current => ({ ...current, [name]: result })); await load(); }
    catch (reason) { failure(reason); }
    finally { setBusy(""); }
  }

  async function testWhisper(name: string) {
    setError("");
    try {
      const request = await api<{ id: string; sample: string }>("/models/whisper/benchmark", json("POST", { name }));
      setWhisperResults(current => ({ ...current, [name]: { status: "pending", sample: request.sample } }));
      for (let attempt = 0; attempt < 400; attempt++) {
        await new Promise(resolve => window.setTimeout(resolve, 2000));
        const result = await api<WhisperResult>(`/models/whisper/benchmark/${request.id}`);
        setWhisperResults(current => ({ ...current, [name]: { ...result, sample: result.sample ?? request.sample } }));
        if (result.status === "done" || result.status === "error") break;
      }
      await load();
    } catch (reason) { failure(reason); }
  }

  async function download(name: string) {
    setError(""); setMessage("");
    setPulls(current => ({ ...current, [name]: { status: "Préparation…" } }));
    try {
      await pullModel(name, pull => setPulls(current => ({ ...current, [name]: pull })));
      setPulls(current => { const next = { ...current }; delete next[name]; return next; });
      setMessage(`${name} est installé.`);
      await load();
    } catch (reason) {
      setPulls(current => ({ ...current, [name]: { status: "échec", error: reason instanceof Error ? reason.message : String(reason) } }));
    }
  }

  async function remove(name: string) {
    if (!window.confirm(`Supprimer ${name} ? Il faudra le télécharger de nouveau pour l'utiliser.`)) return;
    setError("");
    try { await api(`/models/llm/${encodeURIComponent(name)}`, { method: "DELETE" }); await load(); }
    catch (reason) { failure(reason); }
  }

  if (!overview) return <div className="page">{error ? <div className="error">{error}</div> : <p className="muted">Chargement…</p>}</div>;
  const gpu = overview.gpu;
  const usedPercent = gpu?.memory_total_mb ? Math.round(100 * (gpu.memory_used_mb ?? 0) / gpu.memory_total_mb) : null;
  const languageModels = overview.installed.filter(model => !model.embedding);
  const pullRow = (name: string) => {
    const pull = pulls[name];
    if (!pull) return null;
    const percent = pull.total ? Math.floor(100 * (pull.completed ?? 0) / pull.total) : null;
    return <div className="pull-progress">{pull.error ? <span className="error inline-error">{pull.error}</span> : <>
      <div className="progress-track small" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent ?? 0}><div style={{ width: `${percent ?? 0}%` }} /></div>
      <span className="field-hint">{pull.status}{pull.total ? ` · ${formatBytes(pull.completed ?? 0)} / ${formatBytes(pull.total)}` : ""}</span>
    </>}</div>;
  };

  return <div className="page wide">
    <header className="page-header"><h1>Modèles</h1><p>Choisissez les modèles d&apos;IA locaux, mesurez leur vitesse sur cet appareil et surveillez la mémoire de la carte graphique.</p></header>
    {error && <div className="error" role="alert">{error}</div>}
    {message && <p className="success-note" role="status">{message}</p>}

    <section className="card settings-section">
      <h2>Carte graphique</h2>
      {gpu?.name ? <>
        <div className="spread"><strong>{gpu.name}</strong><span className="field-hint">Transcription sur {gpu.whisper_device === "cuda" ? "GPU" : "CPU"} · utilisation {gpu.utilization ?? 0} %</span></div>
        <div className="progress-track vram" role="progressbar" aria-label="Mémoire vidéo utilisée" aria-valuemin={0} aria-valuemax={100} aria-valuenow={usedPercent ?? 0}><div style={{ width: `${usedPercent ?? 0}%` }} /></div>
        <p className="field-hint">Mémoire vidéo : {((gpu.memory_used_mb ?? 0) / 1024).toFixed(1).replace(".", ",")} Go utilisés sur {((gpu.memory_total_mb ?? 0) / 1024).toFixed(1).replace(".", ",")} Go ({usedPercent} %). Au-delà, les modèles débordent en mémoire système et ralentissent fortement.</p>
      </> : <p className="muted">Aucune carte graphique signalée : les modèles tournent sur le processeur{overview.whisper.device === "cpu" ? "" : ", ou le service de transcription en direct est arrêté"}.</p>}
      {!!overview.loaded.length && <><p className="field-label">Chargés dans Ollama en ce moment</p><ul className="model-list compact">{overview.loaded.map(model => <li key={model.name}><span>{model.name}</span><span className="field-hint">{formatBytes(model.vram_bytes || 0)} en mémoire vidéo sur {formatBytes(model.size_bytes || 0)}</span></li>)}</ul></>}
    </section>

    <section className="card settings-section">
      <h2>Modèle de langage (résumés, traduction, chat)</h2>
      <p className="muted">Utilisé : <strong>{overview.llm.current}</strong>{overview.llm.chosen ? ` (réglage par défaut : ${overview.llm.default})` : " (réglage par défaut)"}. Il sert aussi au relevé des personnes et des dates ; la recherche par le sens utilise un modèle à part ({overview.embedding_model}).</p>
      {overview.ollama_error && <div className="status-banner" role="status"><p>{overview.ollama_error}</p></div>}
      <ul className="model-list">{languageModels.map(model => {
        const inUse = same(model.name, overview.llm.current);
        const result = llmResults[model.name];
        return <li key={model.name} className={inUse ? "in-use" : ""}>
          <div className="model-name"><strong>{model.name}</strong><span className="field-hint">{[model.parameter_size, model.quantization, model.size_bytes ? formatBytes(model.size_bytes) : ""].filter(Boolean).join(" · ")}</span></div>
          <div className="row model-actions">
            {inUse ? <span className="pill">utilisé</span> : <button type="button" className="btn small" onClick={() => void choose({ llm_model: same(model.name, overview.llm.default) ? null : model.name })} disabled={busy !== ""}>Utiliser</button>}
            <button type="button" className="btn small" onClick={() => void testLlm(model.name)} disabled={busy !== ""}>{busy === `llm:${model.name}` ? "Test…" : "Tester"}</button>
            {!inUse && !same(model.name, overview.llm.default) && <button type="button" className="btn small danger" onClick={() => void remove(model.name)} disabled={busy !== ""}>Supprimer</button>}
          </div>
          {result && <div className="benchmark">
            <strong>{result.tokens_per_second ?? "?"} jetons/s</strong> en écriture · lecture {result.prompt_tokens_per_second ?? "?"} jetons/s · chargement {result.load_seconds.toFixed(1).replace(".", ",")} s
            <span className="field-hint">Un résumé standard (~700 mots, ~1 400 jetons) s&apos;écrit en ~{result.tokens_per_second ? Math.round(1400 / result.tokens_per_second) : "?"} s. Réponse du test : « {result.answer} »</span>
          </div>}
        </li>;
      })}</ul>
      <p className="field-label">Autres modèles</p>
      <ul className="model-list compact">{overview.suggestions.filter(suggestion => !suggestion.installed).map(suggestion => <li key={suggestion.name}>
        <div className="model-name"><strong>{suggestion.name}</strong><span className="field-hint">{suggestion.vram} · {suggestion.note}</span></div>
        <button type="button" className="btn small" onClick={() => void download(suggestion.name)} disabled={!!pulls[suggestion.name] && !pulls[suggestion.name].error}>Télécharger</button>
        {pullRow(suggestion.name)}
      </li>)}</ul>
      <div className="row custom-model"><input value={customName} onChange={event => setCustomName(event.target.value)} placeholder="Autre modèle Ollama (ex. llama3.1:8b)" aria-label="Nom d'un modèle Ollama" maxLength={120} /><button type="button" className="btn small" onClick={() => { const name = customName.trim(); if (name) { void download(name); setCustomName(""); } }} disabled={!customName.trim()}>Télécharger</button></div>
      {Object.keys(pulls).filter(name => !overview.suggestions.some(item => item.name === name)).map(name => <div key={name}><span className="field-hint">{name}</span>{pullRow(name)}</div>)}
    </section>

    <section className="card settings-section">
      <h2>Modèle de transcription (Whisper)</h2>
      <p className="muted">Utilisé : <strong>{overview.whisper.current}</strong> sur {overview.whisper.device === "cuda" ? "GPU" : "CPU"}{overview.whisper.chosen ? ` (réglage par défaut : ${overview.whisper.default})` : " (réglage par défaut)"}. La transcription en direct garde un petit modèle ({overview.whisper.live}). Le test transcrit la première minute de votre vidéo la plus récente.</p>
      <ul className="model-list">{overview.whisper.choices.map(choice => {
        const inUse = choice.name === overview.whisper.current;
        const result = whisperResults[choice.name];
        return <li key={choice.name} className={inUse ? "in-use" : ""}>
          <div className="model-name"><strong>{choice.name}</strong><span className="field-hint">{choice.size} · {choice.note}{choice.cached ? " · déjà téléchargé" : " · téléchargé au premier usage"}</span></div>
          <div className="row model-actions">
            {inUse ? <span className="pill">utilisé</span> : <button type="button" className="btn small" onClick={() => void choose({ whisper_model: choice.name === overview.whisper.default ? null : choice.name })} disabled={busy !== ""}>Utiliser</button>}
            <button type="button" className="btn small" onClick={() => void testWhisper(choice.name)} disabled={!!result && (result.status === "pending" || result.status === "running")}>{result && (result.status === "pending" || result.status === "running") ? "Test…" : "Tester"}</button>
          </div>
          {result && <div className="benchmark">{result.status === "done"
            ? <><strong>×{String(result.speed).replace(".", ",")} temps réel</strong> · {result.audio_seconds} s d&apos;audio en {String(result.transcribe_seconds).replace(".", ",")} s · chargement {String(result.load_seconds).replace(".", ",")} s<span className="field-hint">Une heure se transcrirait en ~{result.speed ? Math.max(1, Math.round(60 / result.speed)) : "?"} min. Extrait de « {result.sample} » : « {result.text} »</span></>
            : result.status === "error" ? <span className="error inline-error">{result.detail}</span>
            : <span className="field-hint">{result.status === "pending" ? "En attente du service de transcription en direct…" : "Chargement du modèle (téléchargement s'il est nouveau) puis transcription…"}</span>}</div>}
        </li>;
      })}</ul>
    </section>

    <QualityPanel />
  </div>;
}
