import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { isRtl, translator } from "../i18n";
import { PreChat } from "../prechat/PreChat";
import type { ChatController, ChatState } from "../state/controller";
import { ChatIcon, CloseIcon, NewIcon } from "./icons";
import { Message } from "./Message";

export interface AppProps {
  controller: ChatController;
  mode: "launcher" | "inline";
  position: "left" | "right";
  title?: string | null;
  fullpage?: boolean;
  hideHeader?: boolean;
}

function useChat(controller: ChatController): ChatState {
  const [state, setState] = useState(controller.get());
  useEffect(() => {
    const unsubscribe = controller.subscribe(setState);
    setState(controller.get()); // changes between first render and subscribing must not be lost
    return unsubscribe;
  }, [controller]);
  return state;
}

export function App({ controller, mode, position, title, fullpage, hideHeader }: AppProps) {
  const s = useChat(controller);
  const t = translator(s.lang);
  const dir = isRtl(s.lang) ? "rtl" : "ltr";
  const [draft, setDraft] = useState("");
  const bodyRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const launcherRef = useRef<HTMLButtonElement>(null);
  const open = mode === "inline" || s.open;
  const pos = (s.config?.theme.position as "left" | "right" | undefined) ?? position;
  const heading = title || (s.config?.theme.title as string | undefined) || s.config?.assistant.name || t("title");
  const logo = s.config?.theme.logo_url;
  const safeLogo = typeof logo === "string" && /^https:\/\//i.test(logo) ? logo : null;
  const max = s.config?.limits.max_question_chars ?? 2000;

  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [s.messages, open]);

  useEffect(() => {
    if (open && s.phase === "ready" && mode === "launcher") inputRef.current?.focus();
  }, [open, s.phase, mode]);

  const send = () => {
    if (!draft.trim() || s.streaming) return;
    const q = draft;
    setDraft("");
    void controller.send(q);
  };

  const close = () => {
    controller.setOpen(false);
    launcherRef.current?.focus();
  };

  const panel = (
    <section
      class={`panel pos-${pos}`}
      role={mode === "launcher" ? "dialog" : "region"}
      aria-label={heading}
      dir={dir}
      lang={s.lang}
      onKeyDown={(e) => {
        if (e.key === "Escape" && mode === "launcher") close();
      }}
    >
      {!hideHeader && (
        <header class="head">
          {safeLogo && <img src={safeLogo} alt="" />}
          <h2 dir="auto">{heading}</h2>
          {s.phase === "ready" && s.messages.length > 0 && (
            <button type="button" class="icon-btn" aria-label={t("new_chat")} title={t("new_chat")} onClick={() => controller.newConversation()}>
              <NewIcon />
            </button>
          )}
          {mode === "launcher" && (
            <button type="button" class="icon-btn" aria-label={t("close")} title={t("close")} onClick={close}>
              <CloseIcon />
            </button>
          )}
        </header>
      )}
      {s.phase === "loading" && <div class="center">…</div>}
      {s.phase === "unavailable" && <div class="center">{t(s.notice === "rate_limited" ? "rate_limited" : "unavailable")}</div>}
      {s.phase === "prechat" && s.config && <PreChat controller={controller} t={t} />}
      {s.phase === "ready" && s.config && (
        <>
          <div class="body" ref={bodyRef} aria-live="polite" aria-relevant="additions text" aria-busy={s.streaming}>
            <div class="row assistant welcome">
              <div class="bubble" dir="auto">
                {s.config.assistant.welcome_text}
              </div>
            </div>
            {s.messages.map((m) => (
              <Message
                key={m.id}
                msg={m}
                t={t}
                feedbackEnabled={s.config!.features.feedback}
                onFeedback={(id, v) => void controller.feedback(id, v)}
                onRetry={(id) => void controller.retry(id)}
              />
            ))}
          </div>
          {s.notice && (
            <div class="notice" role="status">
              {t(s.notice as Parameters<typeof t>[0])}
            </div>
          )}
          <form
            class="composer"
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
          >
            <label class="sr" for="codura-q">
              {t("placeholder")}
            </label>
            <textarea
              id="codura-q"
              ref={inputRef}
              rows={1}
              dir="auto"
              maxLength={max}
              placeholder={t("placeholder")}
              value={draft}
              onInput={(e) => setDraft((e.target as HTMLTextAreaElement).value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            {s.streaming ? (
              <button type="button" class="send" onClick={() => controller.stop()}>
                {t("stop")}
              </button>
            ) : (
              <button type="submit" class="send" disabled={!draft.trim() || s.offline}>
                {t("send")}
              </button>
            )}
          </form>
        </>
      )}
      {s.config?.branding !== false && (
        <div class="foot">{t("powered_by")}</div>
      )}
    </section>
  );

  return (
    <div class={`root mode-${mode}${fullpage ? " fullpage" : ""}`} dir={dir}>
      {open && panel}
      {mode === "launcher" && !s.open && (
        <button ref={launcherRef} type="button" class={`launcher pos-${pos}`} aria-label={t("open")} aria-expanded={false} onClick={() => controller.setOpen(true)}>
          <ChatIcon />
        </button>
      )}
    </div>
  );
}
