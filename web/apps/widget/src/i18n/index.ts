/** UI strings for the launch languages (NFR-13). Missing keys fall back to `en` (channels.md §3). */
import ar from "./ar.json";
import de from "./de.json";
import en from "./en.json";
import es from "./es.json";
import fr from "./fr.json";
import hi from "./hi.json";
import zh from "./zh.json";

export type Strings = typeof en;
export type StringKey = keyof Strings;

export const CATALOG: Record<string, Partial<Strings>> = { en, hi, es, fr, de, ar, zh };
export const UI_LANGUAGES = Object.keys(CATALOG);
const RTL = new Set(["ar", "he", "fa", "ur"]);

/** "hi-IN" → "hi"; unknown → "en". `auto`/empty uses the browser's preferences in order. */
export function resolveLang(requested?: string | null, preferences: readonly string[] = browserLanguages()): string {
  const candidates = requested && requested !== "auto" ? [requested] : [...preferences];
  for (const c of candidates) {
    const base = c.toLowerCase().split(/[-_]/)[0] ?? "";
    if (base in CATALOG) return base;
  }
  return "en";
}

export function browserLanguages(): string[] {
  const nav = globalThis.navigator;
  if (!nav) return [];
  return nav.languages?.length ? [...nav.languages] : nav.language ? [nav.language] : [];
}

export function isRtl(lang: string): boolean {
  return RTL.has(lang);
}

export function translator(lang: string) {
  const table = CATALOG[lang] ?? {};
  return (key: StringKey, vars?: Record<string, string | number>): string => {
    let s = table[key] ?? en[key] ?? key;
    if (vars) for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, String(v));
    return s;
  };
}

export type T = ReturnType<typeof translator>;
