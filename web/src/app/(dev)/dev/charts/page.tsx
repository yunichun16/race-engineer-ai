import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { IS_DEV } from "@/lib/env";
import { Gallery } from "./Gallery";

export const metadata: Metadata = { title: "Chart gallery", robots: { index: false } };

// Development only: every saved tool result drawn through the site's chart wrappers, with the
// lifecycle checks (plan 4.6). A production build answers 404 here.
export default function ChartGalleryPage() {
  if (!IS_DEV) notFound();
  return (
    <main id="main" tabIndex={-1} className="flex-1">
      <div className="mx-auto max-w-[1120px] px-4 py-8 sm:px-6">
        <h1 className="text-3xl font-semibold tracking-tight text-fg">Chart gallery</h1>
        <p className="mt-2 max-w-[72ch] text-muted">
          Development only. Each saved tool result in <code>src/mcp-app/sample-*.json</code> (telemetry aside, which
          only Claude shows) drawn through the same wrappers the site uses, with the checks for listeners, observers
          and frames left behind. With Glass on, the cards sit on the site&apos;s real surface (the glass panel over
          the ambient light); off, they are opaque on a flat page, as a chart sits in Claude.
        </p>
        <Suspense fallback={null}>
          <Gallery />
        </Suspense>
      </div>
    </main>
  );
}
