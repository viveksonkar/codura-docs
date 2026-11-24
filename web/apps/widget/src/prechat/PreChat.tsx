/** Optional / required Visitor email before the first message (FR-53, ADR-0023). */
import { useState } from "preact/hooks";
import type { T } from "../i18n";
import type { ChatController } from "../state/controller";

export function PreChat({ controller, t }: { controller: ChatController; t: T }) {
  const config = controller.get().config!;
  const { mode, consent_text, privacy_url } = config.visitor_email;
  const [email, setEmail] = useState("");
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const safePrivacy = privacy_url && /^https?:\/\//i.test(privacy_url) ? privacy_url : null;

  const submit = (skip: boolean) => {
    const result = controller.submitPrechat(skip ? null : email, consent);
    setError(result === "ok" ? null : t(result));
  };

  return (
    <form
      class="prechat"
      onSubmit={(e) => {
        e.preventDefault();
        submit(false);
      }}
      noValidate
    >
      <h3>{t("prechat_title")}</h3>
      <label>
        <span class="sr">{t("prechat_email")}</span>
        <input
          type="email"
          autoComplete="email"
          placeholder={t("prechat_email")}
          value={email}
          required={mode === "required"}
          aria-invalid={error === t("prechat_invalid")}
          onInput={(e) => setEmail((e.target as HTMLInputElement).value)}
        />
      </label>
      <label class="consent">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent((e.target as HTMLInputElement).checked)} />
        <span dir="auto">
          {consent_text || t("prechat_consent_default")}{" "}
          {safePrivacy && (
            <a href={safePrivacy} target="_blank" rel="noopener noreferrer nofollow">
              {t("prechat_privacy")}
            </a>
          )}
        </span>
      </label>
      {error && (
        <div class="err" role="alert">
          {error}
        </div>
      )}
      <div class="actions">
        <button type="submit" class="send">
          {t("prechat_start")}
        </button>
        {mode === "optional" && (
          <button type="button" class="link" onClick={() => submit(true)}>
            {t("prechat_skip")}
          </button>
        )}
      </div>
    </form>
  );
}
