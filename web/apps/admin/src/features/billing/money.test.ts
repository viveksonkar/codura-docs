import { describe, expect, it } from "vitest";
import { currencyFor, fromMicros, fromMinor, toMicros } from "./money";

describe("billing money", () => {
  it("India pays in INR, the rest of the world in USD", () => {
    expect(currencyFor("in")).toBe("INR");
    expect(currencyFor("IN")).toBe("INR");
    expect(currencyFor("US")).toBe("USD");
    expect(currencyFor("DE")).toBe("USD");
  });

  it("formats minor units and micros", () => {
    expect(fromMinor(2900, "USD")).toBe("$29.00");
    expect(fromMinor(199900, "INR")).toBe("₹1,999.00");
    expect(fromMicros(29_000_000, "USD")).toBe("$29");
    expect(fromMicros(15_000, "USD", true)).toBe("$0.015");
    expect(fromMicros(1_200_000, "INR", true)).toBe("₹1.20");
  });

  it("parses editor input to micros", () => {
    expect(toMicros("29")).toBe(29_000_000);
    expect(toMicros("0.015")).toBe(15_000);
    expect(toMicros("₹1,999")).toBe(1_999_000_000);
    expect(toMicros("abc")).toBe(0);
  });
});
