/**
 * Model output is untrusted (SEC-07): Markdown → HTML with `marked`, then a strict DOMPurify
 * allowlist. No raw HTML survives, links are http(s)/mailto only and open safely in a new tab.
 */
import DOMPurify from "dompurify";
import { Marked } from "marked";

const ALLOWED_TAGS = ["p", "br", "strong", "em", "del", "ul", "ol", "li", "code", "pre", "blockquote", "a", "h3", "h4", "h5", "table", "thead", "tbody", "tr", "th", "td", "hr"];
const ALLOWED_ATTR = ["href", "target", "rel", "dir"];
const SAFE_URL = /^(?:https?:|mailto:)/i;

const md = new Marked({ gfm: true, breaks: true, async: false });
// Raw HTML in the source is shown as text, not parsed (defence in depth before DOMPurify).
md.use({
  renderer: {
    html({ text }) {
      return escapeHtml(text);
    },
    heading({ tokens, depth }) {
      const level = Math.min(Math.max(depth + 2, 3), 5); // never h1/h2 inside a chat bubble
      return `<h${level}>${this.parser.parseInline(tokens)}</h${level}>`;
    },
  },
});

let hooked = false;
function purifier(): typeof DOMPurify {
  if (!hooked) {
    DOMPurify.addHook("afterSanitizeAttributes", (node) => {
      if (node.nodeName === "A") {
        const href = node.getAttribute("href") ?? "";
        if (!SAFE_URL.test(href)) node.removeAttribute("href");
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer nofollow");
      }
    });
    hooked = true;
  }
  return DOMPurify;
}

export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

export function renderMarkdown(source: string): string {
  const html = md.parse(source) as string;
  return purifier().sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOWED_URI_REGEXP: SAFE_URL,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
  });
}
