import { describe, expect, it } from "vitest";
import en from "./en.json";
import { CATALOG, isRtl, resolveLang, translator } from "./index";

describe("i18n", () => {
  it("has every key in every launch language (Q3)", () => {
    expect(Object.keys(CATALOG).sort()).toEqual(["ar", "de", "en", "es", "fr", "hi", "zh"]);
    for (const [lang, table] of Object.entries(CATALOG)) {
      expect(Object.keys(table).sort(), lang).toEqual(Object.keys(en).sort());
      for (const value of Object.values(table)) expect(value, lang).toBeTruthy();
    }
  });

  it("resolves the locale from the attribute or browser preferences, falling back to en", () => {
    expect(resolveLang("hi-IN")).toBe("hi");
    expect(resolveLang("auto", ["pt-BR", "es-MX"])).toBe("es");
    expect(resolveLang(null, ["ja"])).toBe("en");
    expect(resolveLang("zh_CN")).toBe("zh");
  });

  it("mirrors RTL locales and interpolates", () => {
    expect(isRtl("ar")).toBe(true);
    expect(isRtl("hi")).toBe(false);
    expect(translator("ar")("send")).toBe("إرسال");
    expect(translator("de")("citation_label", { n: 2 })).toBe("Quelle 2");
    expect(translator("xx")("send")).toBe("Send");
  });
});
