/**
 * Shadow-DOM styles. Host pages theme through CSS custom properties that pierce the shadow root:
 * --codura-accent, --codura-radius, --codura-font (channels.md §3). Fonts fall back to system
 * fonts that cover Devanagari, Arabic and CJK (FR-47).
 */
export const STYLES = `
:host { all: initial; --_accent: var(--codura-accent, #2563eb); --_radius: var(--codura-radius, 14px);
  --_font: var(--codura-font, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Noto Sans Devanagari",
    "Nirmala UI", "Mangal", "Noto Sans Arabic", "Segoe UI Arabic", "Geeza Pro", "Noto Sans SC", "PingFang SC",
    "Microsoft YaHei", "Hiragino Sans", sans-serif);
  --_bg: #fff; --_fg: #111827; --_muted: #6b7280; --_line: #e5e7eb; --_soft: #f3f4f6; --_on-accent: #fff;
  font-family: var(--_font); color: var(--_fg); font-size: 15px; line-height: 1.5; }
@media (prefers-color-scheme: dark) { :host(:not([theme="light"])) { --_bg: #111827; --_fg: #f9fafb;
  --_muted: #9ca3af; --_line: #374151; --_soft: #1f2937; } }
:host([theme="dark"]) { --_bg: #111827; --_fg: #f9fafb; --_muted: #9ca3af; --_line: #374151; --_soft: #1f2937; }
*, *::before, *::after { box-sizing: border-box; }
button, input, textarea { font: inherit; color: inherit; }
button { cursor: pointer; }
:focus-visible { outline: 2px solid var(--_accent); outline-offset: 2px; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

.launcher { position: fixed; bottom: 20px; z-index: 2147483000; width: 56px; height: 56px; border-radius: 50%;
  border: 0; background: var(--_accent); color: var(--_on-accent); box-shadow: 0 6px 20px rgba(0,0,0,.25);
  display: grid; place-items: center; }
.launcher svg { width: 26px; height: 26px; }
.pos-right { right: 20px; } .pos-left { left: 20px; }

.panel { display: flex; flex-direction: column; background: var(--_bg); color: var(--_fg); overflow: hidden; }
.mode-launcher .panel { position: fixed; bottom: 88px; z-index: 2147483000; width: min(400px, calc(100vw - 24px));
  height: min(640px, calc(100vh - 110px)); border-radius: var(--_radius); box-shadow: 0 12px 40px rgba(0,0,0,.25);
  border: 1px solid var(--_line); }
.mode-launcher .panel.pos-right { right: 20px; } .mode-launcher .panel.pos-left { left: 20px; }
.mode-inline .panel { width: 100%; height: 100%; min-height: 420px; border: 1px solid var(--_line); border-radius: var(--_radius); }
.fullpage .panel { border: 0; border-radius: 0; min-height: 100vh; min-height: 100dvh; }
@media (max-width: 480px) { .mode-launcher .panel { inset: 0; width: 100vw; height: 100dvh; border-radius: 0; bottom: 0; } }

.head { display: flex; align-items: center; gap: 10px; padding: 12px 14px; background: var(--_accent); color: var(--_on-accent); }
.head img { width: 28px; height: 28px; border-radius: 6px; object-fit: cover; background: #fff; }
.head h2 { flex: 1; margin: 0; font-size: 16px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.icon-btn { border: 0; background: transparent; color: inherit; width: 32px; height: 32px; border-radius: 8px; display: grid; place-items: center; }
.icon-btn:hover { background: rgba(255,255,255,.18); }
.icon-btn svg { width: 18px; height: 18px; }

.body { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; background: var(--_bg); }
.row { display: flex; } .row.user { justify-content: flex-end; }
.bubble { max-width: 86%; padding: 9px 12px; border-radius: 14px; overflow-wrap: anywhere; }
.user .bubble { background: var(--_accent); color: var(--_on-accent); border-end-end-radius: 4px; white-space: pre-wrap; }
.assistant .bubble { background: var(--_soft); border-end-start-radius: 4px; }
.welcome .bubble { background: var(--_soft); }
.md p { margin: 0 0 .5em; } .md p:last-child { margin-bottom: 0; }
.md ul, .md ol { margin: .25em 0 .5em; padding-inline-start: 1.3em; }
.md pre { overflow-x: auto; background: rgba(0,0,0,.06); padding: 8px; border-radius: 6px; }
.md code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .9em; }
.md a { color: var(--_accent); }
.md table { border-collapse: collapse; } .md td, .md th { border: 1px solid var(--_line); padding: 2px 6px; }
.md h3, .md h4, .md h5 { margin: .4em 0 .2em; font-size: 1em; }
.typing { color: var(--_muted); font-style: italic; }
.cursor::after { content: "▍"; animation: blink 1s steps(1) infinite; margin-inline-start: 1px; }
@keyframes blink { 50% { opacity: 0; } }
@media (prefers-reduced-motion: reduce) { .cursor::after { animation: none; } }

.meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 6px; }
.chip { border: 1px solid var(--_line); background: var(--_bg); border-radius: 999px; padding: 1px 8px; font-size: 12px; color: var(--_fg); }
.chip[aria-expanded="true"] { border-color: var(--_accent); }
.pop { margin-top: 6px; padding: 8px 10px; border: 1px solid var(--_line); border-radius: 10px; background: var(--_bg); font-size: 13px; }
.pop strong { display: block; } .pop .where { color: var(--_muted); font-size: 12px; }
.pop blockquote { margin: 6px 0 0; padding-inline-start: 8px; border-inline-start: 3px solid var(--_line); color: var(--_muted); }
.fb { margin-inline-start: auto; display: flex; gap: 2px; }
.fb button { border: 0; background: transparent; border-radius: 6px; padding: 2px 6px; color: var(--_muted); font-size: 14px; }
.fb button[aria-pressed="true"] { color: var(--_accent); background: var(--_bg); }
.err { color: #b91c1c; font-size: 13px; display: flex; gap: 8px; align-items: center; }
.err button, .link { border: 0; background: none; color: var(--_accent); text-decoration: underline; padding: 0; font-size: 13px; }

.notice { margin: 0 14px; padding: 6px 10px; border-radius: 8px; background: #fef3c7; color: #92400e; font-size: 13px; }
.composer { display: flex; gap: 8px; padding: 10px; border-top: 1px solid var(--_line); background: var(--_bg); align-items: flex-end; }
.composer textarea { flex: 1; resize: none; border: 1px solid var(--_line); border-radius: 10px; padding: 8px 10px; max-height: 120px;
  background: var(--_bg); min-height: 40px; }
.send { border: 0; border-radius: 10px; background: var(--_accent); color: var(--_on-accent); padding: 0 14px; height: 40px; font-weight: 600; }
.send:disabled { opacity: .5; cursor: default; }
.foot { text-align: center; font-size: 11px; color: var(--_muted); padding: 4px 0 8px; background: var(--_bg); }
.foot a { color: inherit; }

.prechat { padding: 18px; display: flex; flex-direction: column; gap: 12px; }
.prechat h3 { margin: 0; font-size: 16px; }
.prechat input[type=email] { border: 1px solid var(--_line); border-radius: 10px; padding: 9px 10px; background: var(--_bg); width: 100%; }
.prechat label.consent { display: flex; gap: 8px; align-items: flex-start; font-size: 13px; }
.prechat .actions { display: flex; gap: 10px; align-items: center; }
.center { flex: 1; display: grid; place-items: center; color: var(--_muted); padding: 24px; text-align: center; }
[dir="rtl"] .launcher svg { transform: scaleX(-1); }
`;
