/** SEC-07: an XSS corpus in answers and Citations is neutralised. */
import { describe, expect, it } from "vitest";
import { renderMarkdown } from "./markdown";

const CORPUS = [
  "<script>alert(1)</script>",
  "<img src=x onerror=alert(1)>",
  '<a href="javascript:alert(1)">click</a>',
  "[click](javascript:alert(1))",
  "[click](JaVaScRiPt:alert(1))",
  "[click](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
  "[click](vbscript:msgbox(1))",
  "![x](https://evil.example/x.png)",
  '<iframe src="https://evil.example"></iframe>',
  '<svg onload="alert(1)"><circle/></svg>',
  "<style>body{display:none}</style>",
  '<form action="https://evil.example"><input name=p></form>',
  '<div style="background:url(javascript:alert(1))">x</div>',
  "<math><mi xlink:href=\"javascript:alert(1)\">x</mi></math>",
  '<a href="https://ok.example" onclick="alert(1)">ok</a>',
  "<details open ontoggle=alert(1)>",
  "`<script>alert(1)</script>`",
  '"><script>alert(1)</script>',
  "<object data=\"javascript:alert(1)\"></object>",
  '<base href="https://evil.example/">',
  '<meta http-equiv="refresh" content="0;url=https://evil.example">',
];

describe("renderMarkdown", () => {
  it.each(CORPUS)("neutralises %s", (payload) => {
    const html = renderMarkdown(`Answer: ${payload}`);
    const doc = new DOMParser().parseFromString(`<div>${html}</div>`, "text/html");
    expect(doc.querySelectorAll("script, iframe, svg, style, form, input, object, base, meta, img, math, details").length).toBe(0);
    for (const el of Array.from(doc.querySelectorAll("*"))) {
      for (const attr of Array.from(el.attributes)) {
        expect(attr.name.startsWith("on")).toBe(false);
        expect(attr.name).not.toBe("style");
        if (attr.name === "href") expect(attr.value).toMatch(/^(https?:|mailto:)/i);
      }
    }
  });

  it("keeps safe Markdown and hardens links", () => {
    const html = renderMarkdown("# Title\n\n**Bold** and [docs](https://docs.example/x) [1]\n\n- a\n- b");
    expect(html).toContain("<strong>Bold</strong>");
    expect(html).toContain("<li>a</li>");
    expect(html).toMatch(/<h3>Title<\/h3>/); // no h1 in a chat bubble
    expect(html).toContain('href="https://docs.example/x"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer nofollow"');
  });

  it("renders multilingual and RTL text unchanged", () => {
    expect(renderMarkdown("X200 की वारंटी 24 महीने है")).toContain("वारंटी");
    expect(renderMarkdown("الضمان ٢٤ شهرًا")).toContain("الضمان");
    expect(renderMarkdown("保修期为24个月")).toContain("保修期");
  });
});
