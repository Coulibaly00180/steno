// Mirrors backend/app/analysis_options.py: keep both in sync when the
// coefficients are calibrated (docs/specs/phase-1-resultats-justes.md, R-1).

export type SummaryLength = "short" | "standard" | "detailed";

export const summaryLengths: { value: SummaryLength; label: string; hint: string }[] = [
  { value: "short", label: "Court", hint: "L'essentiel en quelques paragraphes" },
  { value: "standard", label: "Standard", hint: "Grandit avec la durée de la vidéo" },
  { value: "detailed", label: "Détaillé", hint: "Deux fois plus long ; traitement plus long, surtout sans GPU" },
];

export const summaryLengthLabels: Record<string, string> = { short: "court", standard: "standard", detailed: "détaillé" };

const clamp = (value: number, low: number, high: number) => Math.trunc(Math.max(low, Math.min(high, value)));

export function wordBudget(durationSeconds: number, length: SummaryLength): number {
  const minutes = Math.max(0, durationSeconds) / 60;
  const standard = clamp(250 + 8 * Math.max(0, minutes - 15), 250, 1500);
  if (length === "short") return clamp(200 + 2.5 * minutes, 200, 450);
  if (length === "detailed") return clamp(2 * standard, 500, 2500);
  return standard;
}

export const sourceLanguages: { code: string; label: string }[] = [
  { code: "fr", label: "Français" },
  { code: "en", label: "Anglais" },
  { code: "es", label: "Espagnol" },
  { code: "de", label: "Allemand" },
  { code: "it", label: "Italien" },
  { code: "pt", label: "Portugais" },
  { code: "nl", label: "Néerlandais" },
  { code: "ar", label: "Arabe" },
  { code: "zh", label: "Chinois" },
  { code: "ja", label: "Japonais" },
];

export const VOCABULARY_MAX_CHARS = 1000;
export const GLOSSARY_MAX_TERMS = 300;
export const CUSTOM_PROMPT_MAX_CHARS = 2000;

/** Same split and case-insensitive de-duplication as the backend. */
export function splitTerms(raw: string): string[] {
  const seen = new Set<string>();
  const terms: string[] = [];
  for (const piece of raw.split(/[,;\n\r]+/)) {
    const term = piece.trim();
    const key = term.toLocaleLowerCase();
    if (term && !seen.has(key)) { seen.add(key); terms.push(term); }
  }
  return terms;
}

/** Template headings, as the backend extracts them for the final summary. */
export function templateHeadings(prompt: string): string[] {
  return prompt.split("\n").filter(line => /^\s*#{1,6}\s+\S/.test(line)).map(line => line.trim());
}

export function safeStorage(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}
