"use client";

import { useState } from "react";
import { Notice } from "@/components/ui/Notice";
import { apiStatus, useHealth } from "@/lib/api/health";
import type { ResourceState } from "@/lib/api/resource";
import type { HealthResponse } from "@/lib/api/types";
import { IS_DEV } from "@/lib/env";
import { StillWorks } from "./ApiDown";

/**
 * The analysis server's state, as a banner under the page header of /mistakes, /styles and /chat
 * (never on the home page or the report, which don't need the server). It shows nothing while
 * the server is up or still being checked, "Can't reach the analysis server" when it is down,
 * and "Some analyses are unavailable" when it is degraded, with the server's reasons in
 * development. It is polite (`warn`): the parts of the page that failed show `ApiDown` in place.
 *
 * While a recheck runs (Try again, or a call that couldn't reach the server), it keeps showing
 * what the last finished check found, so a down banner doesn't vanish for the seconds the
 * recheck takes and then come back.
 */
export function ApiNotice({ className }: { className?: string }) {
  const health = useHealth();

  // The last finished check, kept across a recheck: React's "storing information from previous
  // renders", a state update during render that settles at once (the hook's state keeps its
  // identity until it changes).
  const [settled, setSettled] = useState<ResourceState<HealthResponse> | null>(null);
  const finished = health.status === "ok" || health.status === "error";
  if (finished && settled !== health) setSettled(health);
  const shown = finished ? health : settled;
  const status = shown ? apiStatus(shown) : "checking";

  if (status === "down") {
    return (
      <Notice tone="warn" title="Can't reach the analysis server" className={className}>
        <p className="wrap-anywhere">
          This page&apos;s analyses need it. <StillWorks />
        </p>
      </Notice>
    );
  }

  if (status === "degraded" && shown?.status === "ok") {
    const problems = IS_DEV ? shown.data.problems : [];
    return (
      <Notice tone="warn" title="Some analyses are unavailable" className={className}>
        {problems.length > 0 ? (
          <ul className="list-disc pl-5 wrap-anywhere">
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        ) : null}
      </Notice>
    );
  }

  return null;
}
