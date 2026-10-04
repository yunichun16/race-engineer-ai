import type { Metadata } from "next";
import { Suspense } from "react";
import { ExplorerSkeleton } from "@/components/styles/ExplorerSkeleton";
import { HowToRead } from "@/components/styles/HowToRead";
import { StylesExplorer } from "@/components/styles/StylesExplorer";

export const metadata: Metadata = {
  title: "Driving styles",
  description:
    "How Formula 1 teammates take corners differently, season by season: braking, minimum speed, throttle and coasting, each with its interval, and a map of every driver's style.",
};

// A static shell: the heading and how to read the charts are in the HTML; the explorer reads its
// state from the URL (useSearchParams), so it sits in Suspense and draws in the browser.
export default function StylesPage() {
  return (
    <div className="mx-auto max-w-[1120px] px-4 pt-8 sm:px-6 sm:pt-12">
      <div className="grid grid-cols-[minmax(0,1fr)] gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,27rem)] lg:items-start lg:gap-12">
        <header className="max-w-[60ch]">
          <p className="micro">Styles</p>
          <h1 className="mt-3 text-[clamp(36px,5vw,56px)] leading-[1.05] font-semibold tracking-tight text-balance text-fg">
            How teammates drive differently
          </h1>
          <p className="mt-5 text-lead text-fg">
            Teammates share a car, so the differences between them are the closest this data gets to driving style.
          </p>
        </header>
        <HowToRead />
      </div>
      <Suspense fallback={<ExplorerSkeleton />}>
        <StylesExplorer />
      </Suspense>
    </div>
  );
}
