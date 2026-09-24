"use client";

import { Fragment, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Icon } from "./Icons";

export type TranscriptLine = {
  key: string; time?: string; start?: number; id?: number; text: string;
  // Speaker label and colour (n°8); shown when it changes from the previous line.
  speakerId?: number | null; speaker?: string; color?: string;
  // Words Whisper was unsure of (n°1): [start, end, probability %] in `text`.
  doubts?: number[][];
};
export type SpeakerOption = { id: number; label: string };
type Match = { line: number; start: number; end: number };

const MIN_QUERY = 2;
const MAX_MATCHES = 1000;
const DEBOUNCE_MS = 150;

/** Lower-cased, accent-free text plus, for each folded character, its index in the original. */
function fold(text: string): { folded: string; origin: number[] } {
  let folded = "";
  const origin: number[] = [];
  for (let index = 0; index < text.length; index++) {
    const piece = text[index].normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase();
    for (let offset = 0; offset < piece.length; offset++) { folded += piece[offset]; origin.push(index); }
  }
  return { folded, origin };
}

/** The line's text with its doubtful words underlined (n°1). */
function withDoubts(text: string, doubts: number[][] | undefined): ReactNode {
  if (!doubts?.length) return text;
  const parts: ReactNode[] = [];
  let cursor = 0;
  for (const [start, end, probability] of doubts) {
    if (start < cursor || end > text.length) continue;
    parts.push(text.slice(cursor, start), <span key={start} className="doubt" title={`Mot incertain : confiance ${probability} %`}>{text.slice(start, end)}</span>);
    cursor = end;
  }
  parts.push(text.slice(cursor));
  return <>{parts.map((part, index) => <Fragment key={index}>{part}</Fragment>)}</>;
}

function findMatches(lines: TranscriptLine[], query: string): { matches: Match[]; truncated: boolean } {
  const needle = fold(query.trim()).folded;
  const matches: Match[] = [];
  if (needle.length < MIN_QUERY) return { matches, truncated: false };
  for (let line = 0; line < lines.length; line++) {
    const { folded, origin } = fold(lines[line].text);
    let from = folded.indexOf(needle);
    while (from !== -1) {
      if (matches.length === MAX_MATCHES) return { matches, truncated: true };
      matches.push({ line, start: origin[from], end: origin[from + needle.length - 1] + 1 });
      from = folded.indexOf(needle, from + needle.length);
    }
  }
  return { matches, truncated: false };
}

export default function TranscriptSearch({ lines, query, onQueryChange, empty, label, playing = -1, onSeek, onEdit, toolbar, speakers = [] }: {
  lines: TranscriptLine[];
  query: string;
  onQueryChange: (value: string) => void;
  empty: string;
  label: string;
  /** Index of the line being played, highlighted and followed. */
  playing?: number;
  onSeek?: (line: TranscriptLine) => void;
  /** Present when the lines can be corrected; resolves once saved. */
  onEdit?: (line: TranscriptLine, text: string, speakerId: number | null) => Promise<void>;
  toolbar?: ReactNode;
  /** Speakers a corrected line can be given. */
  speakers?: SpeakerOption[];
}) {
  const [debounced, setDebounced] = useState(query);
  const [active, setActive] = useState(0);
  const [follow, setFollow] = useState(true);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [draftSpeaker, setDraftSpeaker] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  // n°1: the line of the doubt shown last, to go to the next one.
  const [doubtLine, setDoubtLine] = useState(-1);
  const panel = useRef<HTMLDivElement>(null);
  const doubtful = useMemo(() => lines.flatMap((line, index) => line.doubts?.length ? [index] : []), [lines]);
  const doubtWords = useMemo(() => lines.reduce((total, line) => total + (line.doubts?.length ?? 0), 0), [lines]);

  function nextDoubt() {
    if (!doubtful.length) return;
    const next = doubtful.find(index => index > doubtLine) ?? doubtful[0];
    setDoubtLine(next);
    setFollow(false);
    const container = panel.current;
    const row = container?.querySelector<HTMLElement>(`[data-line="${next}"]`);
    if (container && row) container.scrollTo({ top: row.offsetTop - container.clientHeight / 3, behavior: "smooth" });
  }

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query), DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query]);

  const { matches, truncated } = useMemo(() => findMatches(lines, debounced), [lines, debounced]);
  const byLine = useMemo(() => {
    const grouped = new Map<number, { match: Match; index: number }[]>();
    matches.forEach((match, index) => grouped.set(match.line, [...(grouped.get(match.line) || []), { match, index }]));
    return grouped;
  }, [matches]);
  const searching = debounced.trim().length >= MIN_QUERY;

  useEffect(() => { setActive(0); }, [debounced]);
  useEffect(() => {
    panel.current?.querySelector(`[data-match="${active}"]`)?.scrollIntoView({ block: "center" });
  }, [active, matches]);

  // Follow playback inside the panel only: the page itself must not jump.
  useEffect(() => {
    const container = panel.current;
    if (!follow || searching || editing || playing < 0 || !container) return;
    const row = container.querySelector<HTMLElement>(`[data-line="${playing}"]`);
    if (row) container.scrollTo({ top: row.offsetTop - container.clientHeight / 3, behavior: "smooth" });
  }, [playing, follow, searching, editing]);

  function move(step: number) {
    if (matches.length) setActive(current => (current + step + matches.length) % matches.length);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") { event.preventDefault(); move(event.shiftKey ? -1 : 1); }
    if (event.key === "Escape") { event.preventDefault(); onQueryChange(""); }
  }

  async function save(line: TranscriptLine) {
    if (!onEdit || !draft.trim()) return;
    setSaving(true);
    try { await onEdit(line, draft.trim(), draftSpeaker); setEditing(null); }
    finally { setSaving(false); }
  }

  const counter = !searching ? "" : matches.length ? `${active + 1} / ${matches.length}${truncated ? "+" : ""}` : `Aucun résultat pour « ${debounced.trim()} »`;

  return <div className="transcript">
    <div className="search-bar">
      <input type="search" value={query} onChange={event => onQueryChange(event.target.value)} onKeyDown={onKeyDown} placeholder="Rechercher dans le texte…" aria-label={`Rechercher dans ${label}`} disabled={!lines.length} />
      <span className="search-count mono" aria-live="polite">{counter}</span>
      <button type="button" className="icon-btn" onClick={() => move(-1)} disabled={!matches.length} aria-label="Résultat précédent">↑</button>
      <button type="button" className="icon-btn" onClick={() => move(1)} disabled={!matches.length} aria-label="Résultat suivant">↓</button>
      {!!doubtful.length && <button type="button" className="btn small doubt-next" onClick={nextDoubt} title="Mots dont la transcription est incertaine : à vérifier en premier">Prochain doute <span className="mono">{doubtful.indexOf(doubtLine) + 1 || "–"}/{doubtful.length}</span></button>}
      {!!doubtWords && <span className="field-hint doubt-count">{doubtWords} mot{doubtWords > 1 ? "s" : ""} incertain{doubtWords > 1 ? "s" : ""}</span>}
      {playing >= 0 && <label className="checkbox follow-toggle"><input type="checkbox" checked={follow} onChange={event => setFollow(event.target.checked)} /><span>Suivre la lecture</span></label>}
    </div>
    {toolbar}
    <div className="content-panel transcript-lines" ref={panel}>
      {!lines.length ? empty : lines.map((line, lineIndex) => {
        if (editing === line.key) {
          return <form className="line-edit" key={line.key} data-line={lineIndex} onSubmit={event => { event.preventDefault(); void save(line); }}>
            {line.time && <span className="line-time mono">{line.time}</span>}
            {!!speakers.length && <select className="line-speaker-select" value={draftSpeaker ?? ""} onChange={event => setDraftSpeaker(event.target.value ? Number(event.target.value) : null)} aria-label="Intervenant de cette ligne"><option value="">Aucun intervenant</option>{speakers.map(speaker => <option key={speaker.id} value={speaker.id}>{speaker.label}</option>)}</select>}
            <textarea value={draft} onChange={event => setDraft(event.target.value)} maxLength={2000} rows={2} autoFocus aria-label={`Corriger la ligne ${line.time || lineIndex + 1}`} onKeyDown={event => { if (event.key === "Escape") setEditing(null); if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void save(line); } }} />
            <div className="row"><button className="btn primary" disabled={saving || !draft.trim()}>{saving ? "…" : "Enregistrer"}</button><button type="button" className="btn" onClick={() => setEditing(null)} disabled={saving}>Annuler</button></div>
          </form>;
        }
        const hits = byLine.get(lineIndex) || [];
        let cursor = 0;
        const parts = hits.flatMap(({ match, index }) => {
          const before = line.text.slice(cursor, match.start);
          cursor = match.end;
          return [before, <mark key={index} data-match={index} className={index === active ? "active" : undefined}>{line.text.slice(match.start, match.end)}</mark>];
        });
        return <p key={line.key} data-line={lineIndex} className={[lineIndex === playing ? "playing" : "", lineIndex === doubtLine ? "doubt-focus" : ""].filter(Boolean).join(" ") || undefined}>
          {line.time && (onSeek && line.start !== undefined
            ? <button type="button" className="line-time mono seek" onClick={() => onSeek(line)} title="Lire à partir d'ici">{line.time}</button>
            : <span className="line-time mono">{line.time}</span>)}
          <span className="line-text">{line.speaker && line.speaker !== lines[lineIndex - 1]?.speaker && <b className="line-speaker" style={{ color: line.color }}>{line.speaker}</b>}{hits.length ? <>{parts.map((part, index) => <Fragment key={index}>{part}</Fragment>)}{line.text.slice(cursor)}</> : withDoubts(line.text, line.doubts)}</span>
          {onEdit && <button type="button" className="icon-btn line-edit-btn" aria-label={`Corriger la ligne ${line.time || lineIndex + 1}`} onClick={() => { setEditing(line.key); setDraft(line.text); setDraftSpeaker(line.speakerId ?? null); }}><Icon name="edit" size={13}/></button>}
        </p>;
      })}
    </div>
  </div>;
}
