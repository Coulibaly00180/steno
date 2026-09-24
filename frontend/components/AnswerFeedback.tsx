"use client";

import { useState } from "react";
import { Icon } from "./Icons";
import { rateAnswer } from "../lib/chat";

/** n°18: « utile » / « incorrecte » on an answer; a second click removes the rating. */
export default function AnswerFeedback({ path, initial }: { path: string; initial?: number | null }) {
  const [value, setValue] = useState<number | null>(initial ?? null);
  const [busy, setBusy] = useState(false);

  async function rate(next: number) {
    const wanted = value === next ? null : next;
    setBusy(true);
    try { setValue((await rateAnswer(path, wanted)).feedback); }
    catch { /* the rating is optional: the buttons stay as they were */ }
    finally { setBusy(false); }
  }

  return <span className="answer-feedback" role="group" aria-label="Évaluer la réponse">
    <button type="button" className={`icon-btn small-icon${value === 1 ? " rated-up" : ""}`} onClick={() => void rate(1)} disabled={busy} aria-pressed={value === 1} title="Réponse utile"><Icon name="thumbUp" size={13} /></button>
    <button type="button" className={`icon-btn small-icon${value === -1 ? " rated-down" : ""}`} onClick={() => void rate(-1)} disabled={busy} aria-pressed={value === -1} title="Réponse incorrecte : la signaler"><Icon name="thumbDown" size={13} /></button>
    {value === -1 && <span className="field-hint">Signalée</span>}
  </span>;
}
