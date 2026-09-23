"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../lib/api";
import { templateHeadings } from "../../lib/analysis";
import { Icon } from "../../components/Icons";

type Template = { id: string; name: string; description?: string; prompt: string; is_default: boolean };

const json = (method: string, body?: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });

export default function Templates() {
  const [items, setItems] = useState<Template[]>([]);
  const [editing, setEditing] = useState<Template | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [prompt, setPrompt] = useState("");
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Template | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");

  async function load() {
    try { setItems(await api<Template[]>("/templates")); setError(""); }
    catch (reason) { setError(String(reason)); }
  }
  useEffect(() => { void load(); }, []);

  function notify(message: string) { setToast(message); window.setTimeout(() => setToast(""), 2400); }

  function resetForm() { setEditing(null); setName(""); setDescription(""); setPrompt(""); }

  function startEdit(template: Template) {
    setOpenMenu(null); setEditing(template);
    setName(template.name); setDescription(template.description || ""); setPrompt(template.prompt);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function submit(event: FormEvent) {
    event.preventDefault(); setError(""); setBusy(true);
    try {
      const payload = { name, description, prompt };
      if (editing) await api(`/templates/${editing.id}`, json("PUT", payload));
      else await api("/templates", json("POST", payload));
      notify(editing ? "Template modifié" : "Template créé");
      resetForm(); await load();
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  }

  async function action(template: Template, path: string, message: string) {
    setOpenMenu(null); setError("");
    try { await api(`/templates/${template.id}${path}`, json("POST")); notify(message); await load(); }
    catch (reason) { setError(String(reason)); }
  }

  async function remove() {
    if (!deleteTarget) return;
    setBusy(true); setError("");
    try {
      await api(`/templates/${deleteTarget.id}`, { method: "DELETE" });
      if (editing?.id === deleteTarget.id) resetForm();
      setDeleteTarget(null); notify("Template supprimé"); await load();
    } catch (reason) { setError(String(reason)); setDeleteTarget(null); }
    finally { setBusy(false); }
  }

  const headings = templateHeadings(prompt);

  return <div className="page wide">
    <header className="page-header"><h1>Templates de résumé</h1><p>Créez des instructions réutilisables pour vos réunions, podcasts, formations ou entretiens.</p></header>
    {error && <div className="error" role="alert">{error}</div>}
    <div className="template-grid">
      <form className="card" onSubmit={submit}>
        <h2>{editing ? "Modifier le template" : "Nouveau template"}</h2>
        <div className="field"><label htmlFor="template-name">Nom</label><input id="template-name" value={name} onChange={event => setName(event.target.value)} required maxLength={120} placeholder="Compte-rendu produit" /></div>
        <div className="field"><label htmlFor="template-description">Description</label><input id="template-description" value={description} onChange={event => setDescription(event.target.value)} placeholder="Décisions, risques et prochaines étapes" /></div>
        <div className="field"><label htmlFor="template-prompt">Instructions</label><textarea id="template-prompt" value={prompt} onChange={event => setPrompt(event.target.value)} required rows={9} placeholder={"# Décisions\nDécisions prises, avec leur responsable.\n\n# Actions\nTableau Action | Responsable | Échéance."} /><span className="field-hint">Les titres <code>#</code> deviennent les rubriques du résumé ; le texte sous chaque titre est une consigne pour cette rubrique.</span></div>
        <div className="structure-preview" aria-live="polite"><span className="eyebrow">Structure détectée</span>{headings.length ? <ol>{headings.map((heading, index) => <li key={`${index}-${heading}`}>{heading.replace(/^#+\s*/, "")}</li>)}</ol> : <p className="muted">Aucune rubrique : le résumé suivra les consignes en texte libre.</p>}</div>
        <div className="row"><button className="btn primary" disabled={busy}>{editing ? "Enregistrer" : "Créer le template"}</button>{editing && <button type="button" className="btn" onClick={resetForm} disabled={busy}>Annuler</button>}</div>
      </form>
      <div className="template-list">{items.map(template => <article className={`card card-hover${editing?.id === template.id ? " editing" : ""}`} key={template.id}>
        <div className="spread"><h2>{template.name}</h2><div className="row">{template.is_default && <span className="status-badge status-completed"><i />Défaut</span>}<div className="row-menu"><button className="icon-btn" aria-label={`Actions pour ${template.name}`} aria-expanded={openMenu === template.id} onClick={() => setOpenMenu(openMenu === template.id ? null : template.id)}><Icon name="more" size={15}/></button>{openMenu === template.id && <div className="menu-popover">
          <button onClick={() => startEdit(template)}>Modifier</button>
          <button onClick={() => action(template, "/duplicate", "Template dupliqué")}>Dupliquer</button>
          {!template.is_default && <button onClick={() => action(template, "/default", "Template par défaut modifié")}>Définir par défaut</button>}
          <button className="danger" disabled={template.is_default} title={template.is_default ? "Définissez un autre template par défaut avant de supprimer celui-ci" : undefined} onClick={() => { setOpenMenu(null); setDeleteTarget(template); }}><Icon name="trash" size={14}/>Supprimer</button>
        </div>}</div></div></div>
        {template.description && <p className="muted" style={{ fontSize: 13 }}>{template.description}</p>}
        <div className="template-prompt">{template.prompt}</div>
      </article>)}</div>
    </div>
    {deleteTarget && <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="delete-template-title"><div className="modal"><h2 id="delete-template-title">Supprimer « {deleteTarget.name} » ?</h2><p>Les résumés déjà produits avec ce template sont conservés. Cette action est définitive.</p><div className="modal-actions"><button className="btn" onClick={() => setDeleteTarget(null)} disabled={busy}>Annuler</button><button className="btn primary" style={{ background: "var(--danger)", borderColor: "var(--danger)" }} onClick={remove} disabled={busy}>{busy ? "Suppression…" : "Supprimer"}</button></div></div></div>}
    {toast && <div className="toast">✓ {toast}</div>}
  </div>;
}
