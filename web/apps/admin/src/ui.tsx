import DOMPurify from "dompurify";
import { marked } from "marked";
import { useCallback, useEffect, useState } from "react";
import { ApiError, get } from "./api";

/** Fetch JSON with loading/error state; `reload` re-fetches. */
export function useData<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const reload = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      setData(await get<T>(path));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { data, error, loading, reload, setData };
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return e instanceof Error ? e.message : String(e);
}

export function Alert({ kind = "error", children }: { kind?: "error" | "info" | "ok"; children: React.ReactNode }) {
  if (!children) return null;
  return <div className={`alert alert-${kind}`}>{children}</div>;
}

/** Model output and document text are untrusted: Markdown → sanitised HTML only (SEC-07). */
export function SafeMarkdown({ text }: { text: string }) {
  const html = DOMPurify.sanitize(marked.parse(text, { async: false }) as string, {
    ALLOWED_TAGS: ["p", "br", "strong", "em", "ul", "ol", "li", "code", "pre", "blockquote", "a", "h3", "h4"],
    ALLOWED_ATTR: ["href"],
    ALLOWED_URI_REGEXP: /^(https?:|mailto:)/i,
  });
  return <div className="md" dir="auto" dangerouslySetInnerHTML={{ __html: html }} />;
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge badge-${status}`}>{status}</span>;
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function when(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

export function ReadOnlyNote({ canEdit }: { canEdit: boolean }) {
  return canEdit ? null : (
    <Alert kind="info">You have read-only access (Tenant User). Ask a Tenant Admin to make changes.</Alert>
  );
}
