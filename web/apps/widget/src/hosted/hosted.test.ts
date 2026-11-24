import { describe, expect, it } from "vitest";
import { parseHostedUrl } from "./main";

describe("parseHostedUrl", () => {
  it("accepts /c/{tenant}/{assistant} with ?lang and ?embed", () => {
    expect(parseHostedUrl("/c/demo/help", "?lang=hi&embed=1")).toEqual({ tenant: "demo", assistant: "help", lang: "hi", embed: true });
    expect(parseHostedUrl("/c/acme-co/support/", "")).toEqual({ tenant: "acme-co", assistant: "support", lang: null, embed: false });
  });

  it("rejects anything else", () => {
    for (const p of ["/c/demo", "/c/demo/help/x", "/x/demo/help", "/c/de mo/help", "/c/../help"]) {
      expect(parseHostedUrl(p, "")).toBeNull();
    }
    expect(parseHostedUrl("/c/demo/help", "?lang=<script>")!.lang).toBeNull();
  });
});
