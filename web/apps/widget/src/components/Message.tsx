import type { Citation } from "@codura/chat-client";
import { useState } from "preact/hooks";
import type { T } from "../i18n";
import { renderMarkdown } from "../render/markdown";
import type { ChatMessage } from "../state/controller";

interface Props {
  msg: ChatMessage;
  t: T;
  feedbackEnabled: boolean;
  onFeedback: (id: string, v: 1 | -1) => void;
  onRetry: (id: string) => void;
}

function CitationChips({ items, t }: { items: Citation[]; t: T }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!items.length) return null;
  const current = items.find((c) => c.n === open);
  return (
    <>
      <span class="sr">{t("sources")}:</span>
      {items.map((c) => (
        <button
          type="button"
          class="chip"
          key={c.n}
          aria-expanded={open === c.n}
          aria-label={t("citation_label", { n: c.n })}
          title={c.title}
          onClick={() => setOpen(open === c.n ? null : c.n)}
        >
          [{c.n}]
        </button>
      ))}
      {current && (
        <div class="pop" role="note" dir="auto">
          <strong>{current.title}</strong>
          {(current.page != null || current.heading_path.length > 0) && (
            <span class="where">
              {current.page != null ? `${t("page")} ${current.page}` : ""}
              {current.page != null && current.heading_path.length ? " · " : ""}
              {current.heading_path.join(" › ")}
            </span>
          )}
          {current.snippet && <blockquote>{current.snippet}</blockquote>}
        </div>
      )}
    </>
  );
}

export function Message({ msg, t, feedbackEnabled, onFeedback, onRetry }: Props) {
  if (msg.role === "user") {
    return (
      <div class="row user">
        <div class="bubble" dir="auto">
          <span class="sr">{t("you")}: </span>
          {msg.text}
        </div>
      </div>
    );
  }
  const streaming = msg.status === "streaming";
  const persisted = !msg.id.startsWith("a-");
  return (
    <div class="row assistant">
      <div class="bubble">
        <span class="sr">{t("assistant")}: </span>
        {msg.text ? (
          // Sanitised by renderMarkdown (SEC-07): marked + DOMPurify allowlist.
          <div class={`md${streaming ? " cursor" : ""}`} dir="auto" dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.text) }} />
        ) : streaming ? (
          <span class="typing">{t("thinking")}</span>
        ) : null}
        {msg.status === "error" && (
          <div class="err" role="alert">
            <span>{t((msg.error as Parameters<T>[0]) ?? "error")}</span>
            <button type="button" onClick={() => onRetry(msg.id)}>
              {t("retry")}
            </button>
          </div>
        )}
        {!streaming && msg.status !== "error" && (msg.citations.length > 0 || (feedbackEnabled && persisted && msg.text)) && (
          <div class="meta">
            <CitationChips items={msg.citations} t={t} />
            {feedbackEnabled && persisted && msg.text && (
              <span class="fb">
                <button type="button" aria-pressed={msg.feedback === 1} aria-label={t("feedback_up")} title={t("feedback_up")} onClick={() => onFeedback(msg.id, 1)}>
                  👍
                </button>
                <button type="button" aria-pressed={msg.feedback === -1} aria-label={t("feedback_down")} title={t("feedback_down")} onClick={() => onFeedback(msg.id, -1)}>
                  👎
                </button>
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
