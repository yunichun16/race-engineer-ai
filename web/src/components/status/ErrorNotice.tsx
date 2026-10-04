"use client";

import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { toApiError, type ApiError } from "@/lib/api/client";
import { describe } from "@/lib/api/describe";
import { ApiDown } from "./ApiDown";

export interface ErrorNoticeProps {
  /** Usually an ApiError (a hook's `error`). Anything else shows as a fault in the page. */
  error: ApiError | unknown;
  /** Try again. Offered only for errors where the same request can succeed. */
  onRetry?: () => void;
  className?: string;
}

/**
 * A failed API call, in the words of the states catalogue (`describe`). The server's own message
 * shows unchanged where it helps the reader (an event or driver the data doesn't have). An
 * unreachable server shows `ApiDown`.
 */
export function ErrorNotice({ error, onRetry, className }: ErrorNoticeProps) {
  const e = toApiError(error);
  if (e.code === "unreachable") return <ApiDown onRetry={onRetry} className={className} />;
  const copy = describe(e);
  return (
    <Notice
      tone={copy.tone}
      title={copy.title}
      className={className}
      action={
        copy.retry && onRetry ? (
          <Button variant="secondary" size="sm" onClick={onRetry}>
            Try again
          </Button>
        ) : undefined
      }
    >
      {copy.body ? <p className="whitespace-pre-line wrap-anywhere">{copy.body}</p> : null}
    </Notice>
  );
}
