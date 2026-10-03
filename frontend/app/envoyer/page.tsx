"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import LinkImport, { type LinkItem } from "../../components/LinkImport";
import { Icon } from "../../components/Icons";
import { api } from "../../lib/api";
import { sharedLink } from "../../lib/access";

type Template = { id: string; name: string; is_default: boolean };
type Job = { id: string; video_id: string };

/**
 * Feuille de route n° 4, phase 1: the page opened by the "Envoyer à Sténo" bookmarklet and by
 * Android's share sheet (share_target of app/manifest.ts). The link is checked at once; queueing
 * it takes a click here, so no other site can start an analysis by opening this address.
 */
export default function SendPage() {
  const router = useRouter();
  const [link, setLink] = useState<string | null | undefined>(undefined);
  const [checkAtOnce, setCheckAtOnce] = useState(false);
  const [items, setItems] = useState<LinkItem[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState("");
  const [diarize, setDiarize] = useState(false);
  const [numSpeakers, setNumSpeakers] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    // Android puts the shared link in `text` (sometimes with words around it), browsers in `url`.
    setLink(sharedLink(params.get("url"), params.get("text"), params.get("title")));
    // The bookmarklet (noreferrer) and Android's share come without a referrer: the link is checked at once.
    // Sent here by another site, Sténo waits for "Vérifier le lien" before fetching anything.
    let referrerOrigin = "";
    try { referrerOrigin = document.referrer ? new URL(document.referrer).origin : ""; } catch { referrerOrigin = "invalid"; }
    setCheckAtOnce(!referrerOrigin || referrerOrigin === window.location.origin);
    api<Template[]>("/templates").then(rows => {
      setTemplates(rows);
      const fallback = rows.find(row => row.is_default);
      if (fallback) setTemplateId(fallback.id);
    }).catch(() => setTemplates([]));
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const chosen = items.filter(item => item.selected);
    if (!chosen.length || !confirmed) return;
    setBusy(true); setError(""); setDone("");
    const jobs: Job[] = [];
    const failures: string[] = [];
    for (const item of chosen) {
      try {
        jobs.push(await api<Job>("/imports/url", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
          url: item.url, title: item.title, template_id: templateId || null, diarize, num_speakers: diarize && numSpeakers ? Number(numSpeakers) : null,
        }) }));
      } catch (reason) { failures.push(`${item.title} : ${reason instanceof Error ? reason.message : String(reason)}`); }
    }
    setBusy(false);
    if (chosen.length === 1 && jobs.length === 1) { router.push(`/videos/${jobs[0].video_id}?job=${jobs[0].id}`); return; }
    if (failures.length) setError(failures.join(" · "));
    if (jobs.length) setDone(`${jobs.length} import${jobs.length > 1 ? "s" : ""} ajouté${jobs.length > 1 ? "s" : ""} à la file.`);
  }

  return <div className="page send-page">
    <header className="page-header"><h1><Icon name="link" size={20} /> Envoyer à Sténo</h1><p>Le lien reçu est vérifié ; choisissez les options, puis lancez l&apos;analyse.</p></header>
    {link === null && <div className="card"><p>Aucun lien http:// ou https:// n&apos;a été reçu.</p><Link className="text-link" href="/">Importer un fichier ou un lien</Link></div>}
    {link && <form onSubmit={submit} className="send-form">
      <LinkImport key={link} initialUrl={link} checkAtOnce={checkAtOnce}items={items} onItems={setItems} confirmed={confirmed} onConfirmed={setConfirmed} disabled={busy} />
      <div className="card send-options">
        <div className="field"><label htmlFor="send-template">Template de résumé</label><div className="field-control"><select id="send-template" value={templateId} onChange={event => setTemplateId(event.target.value)}><option value="">Par défaut</option>{templates.map(template => <option value={template.id} key={template.id}>{template.name}</option>)}</select><Icon name="chevron" size={14} /></div></div>
        <div className="field speakers-option"><label className="checkbox"><input type="checkbox" checked={diarize} onChange={event => setDiarize(event.target.checked)} /><span>Identifier les intervenants</span></label>{diarize && <label className="speaker-count">Nombre d&apos;intervenants <input type="number" min={1} max={20} value={numSpeakers} onChange={event => setNumSpeakers(event.target.value)} placeholder="auto" /></label>}</div>
        <div className="row"><button className="btn primary" disabled={busy || !confirmed || !items.some(item => item.selected)}>{busy ? "Envoi…" : "Analyser"}</button><Link className="text-link" href="/">Plus d&apos;options dans Importer</Link></div>
      </div>
      {error && <div className="error" role="alert">{error}</div>}
      {done && <p className="success-note" role="status">{done} <Link className="text-link" href="/library">Voir la bibliothèque</Link></p>}
    </form>}
  </div>;
}
