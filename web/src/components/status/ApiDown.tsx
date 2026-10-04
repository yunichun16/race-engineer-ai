"use client";

import Link from "next/link";
import { useSyncExternalStore } from "react";
import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { recheckHealth } from "@/lib/api/health";
import { IS_DEV } from "@/lib/env";

const LINK = "underline decoration-line-strong underline-offset-2 hover:text-fg hover:decoration-current";

/** "The findings and the example on the home page still work.": no dead end, even with no server. */
export function StillWorks() {
  return (
    <>
      The{" "}
      <Link href="/report" className={LINK}>
        findings
      </Link>{" "}
      and the{" "}
      <Link href="/" className={LINK}>
        example on the home page
      </Link>{" "}
      still work.
    </>
  );
}

const never = () => () => {};

// The page's own origin, for the CORS hint. The server (and hydration) has none, so the hint
// names it in words until the browser's value takes over.
function useOrigin(): string | null {
  return useSyncExternalStore(
    never,
    () => window.location.origin,
    () => null,
  );
}

export interface ApiDownProps {
  /** Called with the health recheck on Try again: the call that failed, to make again. */
  onRetry?: () => void;
  className?: string;
}

/**
 * In place of something that needs the analysis server, when it can't be reached. Try again
 * rechecks the server's health (which updates every banner) and calls `onRetry`. In development
 * it says how to start the API and what to check when it is running but the browser can't reach
 * it (the API's CORS list).
 */
export function ApiDown({ onRetry, className }: ApiDownProps) {
  const origin = useOrigin();
  return (
    <Notice
      tone="error"
      title="Can't reach the analysis server."
      className={className}
      action={
        <Button
          variant="secondary"
          size="sm"
          onClick={() => {
            recheckHealth();
            onRetry?.();
          }}
        >
          Try again
        </Button>
      }
    >
      <p className="wrap-anywhere">
        <StillWorks />
      </p>
      {IS_DEV ? (
        <p className="mt-2 wrap-anywhere">
          Start it with <code>make api-fake</code> (or the <code>api-fake</code> entry in .claude/launch.json). If
          it&apos;s running, check that {origin ? <code>{origin}</code> : "this site's address"} is in{" "}
          <code>RACE_ENGINEER_CORS_ORIGINS</code>.
        </p>
      ) : null}
    </Notice>
  );
}
