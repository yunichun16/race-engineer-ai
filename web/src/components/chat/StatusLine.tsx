/**
 * What the answer is doing now: the latest progress note from the server (any wording, cut to one
 * line), or "Answering…" before the first one. The spinner holds still with reduced motion. Not a
 * live region: the page's own status region announces the steps (ChatApp).
 */
export function StatusLine({ note }: { note: string | undefined }) {
  return (
    <p className="flex min-w-0 items-center gap-2 text-sm text-muted">
      <span className="spinner" aria-hidden="true" />
      <span className="min-w-0 truncate">{note?.trim() || "Answering…"}</span>
    </p>
  );
}
