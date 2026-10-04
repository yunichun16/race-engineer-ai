import type { Metadata } from "next";
import { Suspense } from "react";
import { ExplorerSkeleton } from "@/components/mistakes/ExplorerSkeleton";
import { MistakesExplorer } from "@/components/mistakes/MistakesExplorer";
import { ApiNotice } from "@/components/status/ApiNotice";
import { site } from "@/content/site";

export const metadata: Metadata = {
  title: "Mistake explorer",
  description:
    "Pick an F1 session to see the corners the model flagged, ranked by time lost, and open one to see that lap's telemetry against the driver's usual.",
};

/**
 * /mistakes (plan 7.3): race → flagged corners → telemetry. A static shell; the explorer renders
 * in the browser from the URL's state, inside Suspense (it reads `useSearchParams`). The API's
 * status banner sits under the header: this page needs the analysis server.
 */
export default function MistakesPage() {
  return (
    <div className="mx-auto max-w-[1120px] px-4 pt-8 pb-12 sm:px-6 sm:pt-12 lg:pb-15">
      <header className="max-w-[72ch]">
        <p className="micro">Mistakes</p>
        <h1 className="mt-3 text-[clamp(36px,5vw,56px)] leading-[1.05] font-semibold tracking-tight text-balance text-fg">
          Mistake explorer
        </h1>
        <p className="mt-5 text-lead text-fg">
          Pick a session to see the corners the model flagged, ranked by time lost. Open one to see that lap&apos;s telemetry
          against the driver&apos;s usual.
        </p>
        <p className="mt-3 text-sm font-medium text-fg">{site.caveat}</p>
      </header>
      <ApiNotice className="mt-6" />
      <Suspense fallback={<ExplorerSkeleton />}>
        <MistakesExplorer />
      </Suspense>
    </div>
  );
}
