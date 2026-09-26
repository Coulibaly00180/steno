"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, formatBytes } from "../lib/api";

type Model = {
  name: string; description: string; capabilities: string[]; sizes: string[]; pulls: number | null; updated: string | null; installed: boolean;
};
type Catalog = { models: Model[]; fetched_at: number | null; stale: boolean; error: string | null };
type Tag = {
  name: string; size_bytes: number | null; context: string | null; input: string | null; latest: boolean;
  installed: boolean; vram_bytes: number | null; fits: boolean | null;
};

const filters = [
  { value: "", label: "Tous" }, { value: "tools", label: "Outils" }, { value: "thinking", label: "Raisonnement" },
  { value: "vision", label: "Images" }, { value: "embedding", label: "Recherche (embeddings)" },
];
const SHOWN = 30;

function pulls(value: number | null) {
  if (value == null) return "";
  if (value >= 1e6) return `${(value / 1e6).toFixed(1).replace(".", ",")} M téléchargements`;
  if (value >= 1e3) return `${Math.round(value / 1e3)} k téléchargements`;
  return `${value} téléchargements`;
}

function age(seconds: number | null) {
  if (!seconds) return "";
  const minutes = Math.round((Date.now() / 1000 - seconds) / 60);
  if (minutes < 2) return "à l'instant";
  if (minutes < 120) return `il y a ${minutes} min`;
  return `il y a ${Math.round(minutes / 60)} h`;
}

function Variants({ name, onDownload, progress, busy }: {
  name: string; onDownload: (tag: string) => void; progress: (tag: string) => ReactNode; busy: (tag: string) => boolean;
}) {
  const [tags, setTags] = useState<Tag[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<{ tags: Tag[] }>(`/models/catalog/${encodeURIComponent(name)}`).then(result => setTags(result.tags)).catch(reason => setError(reason instanceof Error ? reason.message : String(reason)));
  }, [name]);
  if (error) return <p className="error inline-error">{error}</p>;
  if (!tags) return <p className="field-hint">Lecture des variantes sur ollama.com…</p>;
  return <table className="catalog-variants"><tbody>{tags.map(tag => <tr key={tag.name}>
    <td className="mono">{tag.name}{tag.latest ? <span className="pill">latest</span> : null}</td>
    <td>{tag.size_bytes ? formatBytes(tag.size_bytes) : "—"}</td>
    <td className="field-hint">{tag.context ? `contexte ${tag.context}` : ""}{tag.input ? ` · ${tag.input.toLowerCase()}` : ""}</td>
    <td>{tag.vram_bytes ? <span className={tag.fits === false ? "quality-worse" : tag.fits ? "service-ok" : "field-hint"} title="Estimation avec le contexte de 32 000 jetons de Sténo">≈ {formatBytes(tag.vram_bytes)}{tag.fits === false ? " : trop grand pour la carte" : tag.fits ? " : tient dans la carte" : ""}</span> : null}</td>
    <td>{tag.installed ? <span className="pill">installé</span> : <button type="button" className="btn small" onClick={() => onDownload(tag.name)} disabled={busy(tag.name)}>Télécharger</button>}{progress(tag.name)}</td>
  </tr>)}</tbody></table>;
}

/**
 * The models of ollama.com, read on the site every 6 hours: nothing to type, a
 * model withdrawn from the catalog disappears from the list, and each variant
 * says whether it fits in this computer's graphics card.
 */
export default function OllamaCatalog({ onDownload, progress, busy }: {
  onDownload: (tag: string) => void; progress: (tag: string) => ReactNode; busy: (tag: string) => boolean;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [query, setQuery] = useState("");
  const [capability, setCapability] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [shown, setShown] = useState(SHOWN);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    try { setCatalog(await api<Catalog>(`/models/catalog${refresh ? "?refresh=true" : ""}`)); }
    catch (reason) { setCatalog({ models: [], fetched_at: null, stale: true, error: reason instanceof Error ? reason.message : String(reason) }); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setShown(SHOWN); }, [query, capability]);

  const matching = useMemo(() => {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return (catalog?.models ?? []).filter(model =>
      (!capability || model.capabilities.includes(capability))
      && words.every(word => `${model.name} ${model.description}`.toLowerCase().includes(word)));
  }, [catalog, query, capability]);

  return <div className="ollama-catalog">
    <div className="spread">
      <p className="field-label">Catalogue Ollama{catalog?.models.length ? ` · ${catalog.models.length} modèles` : ""}{catalog?.fetched_at ? ` · lu ${age(catalog.fetched_at)}` : ""}</p>
      <button type="button" className="btn small" onClick={() => void load(true)} disabled={loading}>{loading ? "Lecture…" : "Actualiser"}</button>
    </div>
    {catalog?.error && <div className="status-banner" role="status"><p>{catalog.stale && catalog.models.length ? `Catalogue affiché de la dernière lecture : ${catalog.error}.` : catalog.error}</p></div>}
    <div className="row catalog-filters">
      <input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Chercher un modèle (ex. mistral, français, code)" aria-label="Chercher dans le catalogue Ollama" />
      <div className="segmented" role="radiogroup" aria-label="Capacité">{filters.map(option => <button type="button" role="radio" aria-checked={capability === option.value} className={capability === option.value ? "selected" : ""} key={option.value} onClick={() => setCapability(option.value)}>{option.label}</button>)}</div>
    </div>
    {catalog && !matching.length && !catalog.error && <p className="muted">Aucun modèle ne correspond.</p>}
    <ul className="catalog-list">{matching.slice(0, shown).map(model => <li key={model.name}>
      <button type="button" className="catalog-model" onClick={() => setOpen(current => current === model.name ? null : model.name)} aria-expanded={open === model.name}>
        <span className="catalog-name"><strong>{model.name}</strong>{model.installed && <span className="pill">installé</span>}{model.capabilities.map(item => <span key={item} className="catalog-chip">{item}</span>)}</span>
        <span className="catalog-description">{model.description}</span>
        <span className="field-hint">{[model.sizes.join(" · "), pulls(model.pulls)].filter(Boolean).join(" — ")}</span>
      </button>
      {open === model.name && <Variants name={model.name} onDownload={onDownload} progress={progress} busy={busy} />}
    </li>)}</ul>
    {matching.length > shown && <div className="load-more"><button type="button" className="btn small" onClick={() => setShown(value => value + SHOWN)}>Afficher plus ({shown} sur {matching.length})</button></div>}
    <p className="field-hint">Lu sur ollama.com (toutes les 6 heures) ; un modèle retiré du catalogue en disparaît. Les modèles servis seulement par le cloud d&apos;Ollama ne sont pas proposés : Sténo fait tout tourner sur cet ordinateur.</p>
  </div>;
}
