/** Money display (specs/billing.md §1): prices are micros, invoice amounts are minor units. */
export type Currency = "USD" | "INR";

const LOCALE: Record<Currency, string> = { USD: "en-US", INR: "en-IN" };

export function fromMinor(minor: number, currency: Currency): string {
  return new Intl.NumberFormat(LOCALE[currency], { style: "currency", currency }).format(minor / 100);
}

/** Prices in micros; per-unit overage shows extra decimals so $0.015 doesn't round to $0.02. */
export function fromMicros(micros: number, currency: Currency, perUnit = false): string {
  const value = micros / 1_000_000;
  return new Intl.NumberFormat(LOCALE[currency], {
    style: "currency",
    currency,
    minimumFractionDigits: perUnit ? 2 : value % 1 === 0 ? 0 : 2,
    maximumFractionDigits: perUnit ? 4 : 2,
  }).format(value);
}

export function usd(micros: number): string {
  return fromMicros(micros, "USD");
}

/** India pays in INR, everyone else in USD (ADR-0026). */
export function currencyFor(country: string): Currency {
  return country.trim().toUpperCase() === "IN" ? "INR" : "USD";
}

export function toMicros(text: string): number {
  const n = Number(text.replace(/[^0-9.]/g, ""));
  return Number.isFinite(n) ? Math.round(n * 1_000_000) : 0;
}

export const COUNTRIES: [string, string][] = [
  ["IN", "India"], ["US", "United States"], ["GB", "United Kingdom"], ["DE", "Germany"], ["FR", "France"],
  ["ES", "Spain"], ["AE", "United Arab Emirates"], ["SG", "Singapore"], ["AU", "Australia"], ["CA", "Canada"],
  ["NL", "Netherlands"], ["SA", "Saudi Arabia"], ["CN", "China"], ["JP", "Japan"], ["BR", "Brazil"],
];

export const METER_LABEL: Record<string, string> = {
  answer: "Answers", page_ocr: "OCR pages", pages_stored: "Pages stored", storage_mb: "Storage (MB)",
  seats: "Users", assistants: "Assistants", page_ingested: "Pages ingested", answer_custom_llm: "Custom LLM answers",
};
