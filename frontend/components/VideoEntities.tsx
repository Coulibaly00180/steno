"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "../lib/api";

type Chip = { id: number; name: string; kind: string; kind_label: string; mentions: number; first_seconds: number };
const MAX_SHOWN = 14;

/** n°16: the people, organisations, places and dates named in the video, each opening its page. */
export default function VideoEntities({ videoId, refreshKey }: { videoId: string; refreshKey?: unknown }) {
  const [chips, setChips] = useState<Chip[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [all, setAll] = useState(false);

  useEffect(() => {
    api<{ status: string | null; entities: Chip[] }>(`/videos/${videoId}/entities`)
      .then(result => { setChips(result.entities); setStatus(result.status); })
      .catch(() => setChips([]));
  }, [videoId, refreshKey]);

  if (!chips.length) return status === null || status === "STALE"
    ? <p className="field-hint entity-pending">Personnes, organisations et dates : relevé en arrière-plan…</p>
    : null;
  const shown = all ? chips : chips.slice(0, MAX_SHOWN);
  return <div className="entity-chips" aria-label="Personnes, organisations, lieux et dates cités">
    {shown.map(chip => <Link key={chip.id} className={`entity-chip kind-${chip.kind}`} href={`/entities/${chip.id}`} title={`${chip.kind_label} · cité ${chip.mentions} fois`}>{chip.name}</Link>)}
    {chips.length > MAX_SHOWN && <button type="button" className="text-link entity-more" onClick={() => setAll(value => !value)}>{all ? "moins" : `+ ${chips.length - MAX_SHOWN}`}</button>}
  </div>;
}
