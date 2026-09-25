"use client";

import { useState } from "react";
import { API } from "../lib/api";
import { Icon } from "./Icons";

/** Every download of a processed video: reports, notes, e-mail draft, transcripts, subtitles. */
export default function VideoExports({ videoId, translation, translationOutdated, chapters }: {
  videoId: string; translation: boolean; translationOutdated: boolean; chapters: boolean;
}) {
  // What the DOCX/PDF report carries as its annex (n°20).
  const [annex, setAnnex] = useState<"original" | "translation" | "none">("original");
  return <div className="row exports"><a className="btn primary" href={`${API}/videos/${videoId}/exports/report.docx?transcript=${annex}`}><Icon name="download" size={14}/>Compte-rendu .docx</a><a className="btn" href={`${API}/videos/${videoId}/exports/report.pdf?transcript=${annex}`}><Icon name="download" size={14}/>Compte-rendu .pdf</a><label className="annex-choice">Annexe <select value={annex} onChange={event => setAnnex(event.target.value as typeof annex)} aria-label="Transcription jointe au compte-rendu"><option value="original">transcription</option>{translation && <option value="translation">traduction{translationOutdated ? " (avant correction)" : ""}</option>}<option value="none">aucune</option></select></label><a className="btn" href={`${API}/videos/${videoId}/exports/summary.md`}>Résumé .md</a><a className="btn" href={`${API}/videos/${videoId}/note.md`} title="Note Markdown pour Obsidian : métadonnées, résumé, actions à cocher, liens vers les personnes">Note Obsidian .md</a><a className="btn" href={`${API}/videos/${videoId}/email.eml`} title="S'ouvre dans Outlook ou Thunderbird comme un nouveau message à envoyer"><Icon name="mail" size={14}/>Brouillon d&apos;e-mail</a><a className="btn" href={`${API}/videos/${videoId}/exports/transcript.txt`}><Icon name="download" size={14}/>Transcript .txt</a><a className="btn" href={`${API}/videos/${videoId}/exports/transcript.srt`}>Sous-titres .srt</a><a className="btn" href={`${API}/videos/${videoId}/exports/transcript.vtt`}>Sous-titres .vtt</a>{chapters && <a className="btn" href={`${API}/videos/${videoId}/exports/chapters.txt`}>Chapitres .txt</a>}{translation && <><a className="btn" href={`${API}/videos/${videoId}/exports/translation.txt`}>Traduction .txt</a><a className="btn" href={`${API}/videos/${videoId}/exports/translation.srt`}>Sous-titres traduits .srt</a><a className="btn" href={`${API}/videos/${videoId}/exports/translation.vtt`}>Sous-titres traduits .vtt</a></>}<a className="btn" href={`${API}/videos/${videoId}/exports/metadata.json`}>Metadata .json</a></div>;
}
