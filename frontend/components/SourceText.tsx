import Link from "next/link";
import { Fragment } from "react";
import { formatDuration } from "../lib/api";
import type { Source } from "../lib/chat";
import { InlineBold } from "./TimestampText";

const CITATION = /\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]/g;

export const sourceHref = (source: Source) => `/videos/${source.video_id}?t=${Math.floor(source.start_seconds)}`;

/** An answer whose "[2]" or "[1, 3]" citations open the cited video at the cited moment (n°19). */
export default function SourceText({ text, sources }: { text: string; sources: Source[] }) {
  const byNumber = new Map(sources.map(source => [source.n, source]));
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(CITATION)) {
    const numbers = match[1].split(",").map(value => Number(value.trim()));
    // Unknown numbers stay plain text: the model may write "[2024]"-like brackets.
    if (!numbers.every(number => byNumber.has(number))) continue;
    parts.push(<InlineBold key={`t${last}`} text={text.slice(last, match.index)} />);
    parts.push(<Fragment key={match.index}>{numbers.map(number => {
      const source = byNumber.get(number)!;
      return <Link key={number} className="citation" href={sourceHref(source)} title={`${source.title} · ${formatDuration(source.start_seconds)}`}>{number}</Link>;
    })}</Fragment>);
    last = match.index! + match[0].length;
  }
  parts.push(<InlineBold key="end" text={text.slice(last)} />);
  return <>{parts}</>;
}
