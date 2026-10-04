"use client";

import Link from "next/link";
import { useEffect } from "react";
import { SiteFooter } from "@/components/layout/SiteFooter";
import { Button } from "@/components/ui/Button";

// A page threw while rendering. The reader gets plain words and a way out; the error itself
// goes to the console, never onto the page. `retry` re-fetches and re-renders the segment.
// It replaces the route group's layout, so it brings its own <main> and footer.
export default function ErrorPage({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <>
      <main id="main" tabIndex={-1} className="flex-1">
        <div className="mx-auto max-w-[1120px] px-4 py-12 sm:px-6 sm:py-18">
          <div className="max-w-[72ch]">
            <h1 className="text-[clamp(36px,5vw,56px)] leading-[1.05] font-semibold tracking-tight text-fg">Something went wrong on this page.</h1>
            <p className="mt-4 text-[17px]/[1.7] text-muted">Trying again often works. If it doesn&apos;t, the rest of the site still does.</p>
            <div className="mt-6 flex flex-wrap items-center gap-4">
              <Button variant="primary" onClick={() => retry()}>
                Try again
              </Button>
              <Link href="/" className="link inline-flex min-h-11 items-center">
                Go to the home page
              </Link>
            </div>
          </div>
        </div>
      </main>
      <SiteFooter />
    </>
  );
}
