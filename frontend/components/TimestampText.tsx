"use client";

import { Fragment } from "react";

// "[01:02:03]", "[02:03]" or a bare "01:02:03". A bare "14:30" is left alone:
// in a meeting summary it is usually a time of day, not a position in the video.
const TIMESTAMP = /\[(\d{1,2}(?::\d{2}){1,2})\]|(?<![\d:])(\d{1,2}:\d{2}:\d{2})(?![\d:])/g;

const BOLD = /\*\*([^*\n]+?)\*\*/g;
// "**[02:12:13]**": the timestamp becomes a button, the markers would stay orphaned.
const BOLD_TIMESTAMP = /\*\*(\[[\d:]+\])\*\*/g;

/** Chat answers use Markdown bold; the rest of their Markdown reads fine as text. */
export function InlineBold({ text }: { text: string }) {
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(BOLD)) {
    parts.push(text.slice(cursor, match.index), <strong key={match.index}>{match[1]}</strong>);
    cursor = match.index! + match[0].length;
  }
  parts.push(text.slice(cursor));
  return <>{parts}</>;
}

export function clockToSeconds(clock: string): number | null {
  const parts = clock.split(":").map(Number);
  if (parts.some(part => !Number.isInteger(part)) || parts.slice(1).some(part => part >= 60)) return null;
  return parts.reduce((total, part) => total * 60 + part, 0);
}

/** Text in which video positions become buttons that seek the player (n°2). */
export default function TimestampText({ text: raw, duration, onSeek }: { text: string; duration: number; onSeek?: (seconds: number) => void }) {
  const text = raw.replace(BOLD_TIMESTAMP, "$1");
  if (!onSeek) return <InlineBold text={text} />;
  const parts: (string | { label: string; seconds: number })[] = [];
  let cursor = 0;
  for (const match of text.matchAll(TIMESTAMP)) {
    const clock = match[1] ?? match[2];
    const seconds = clockToSeconds(clock);
    // Ignore positions beyond the end: the model may have made them up.
    if (seconds === null || seconds > duration + 1) continue;
    parts.push(text.slice(cursor, match.index), { label: match[0], seconds });
    cursor = (match.index ?? 0) + match[0].length;
  }
  parts.push(text.slice(cursor));
  return <>{parts.map((part, index) => typeof part === "string"
    ? <Fragment key={index}><InlineBold text={part} /></Fragment>
    : <button type="button" key={index} className="timestamp-link mono" onClick={() => onSeek(part.seconds)} title="Aller à ce moment de la vidéo">{part.label}</button>)}</>;
}
