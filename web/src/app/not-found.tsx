import type { Metadata } from "next";
import Link from "next/link";
import { NAV_ITEMS } from "@/components/layout/nav";
import { SiteFooter } from "@/components/layout/SiteFooter";

export const metadata: Metadata = { title: "No page here" };

// Any address the site doesn't have. It renders under the root layout (header only), so it
// brings its own <main> and footer, and names every section that does exist.
export default function NotFound() {
  return (
    <>
      <main id="main" tabIndex={-1} className="flex-1">
        <div className="mx-auto max-w-[1120px] px-4 py-12 sm:px-6 sm:py-18">
          <div className="max-w-[72ch]">
            <h1 className="text-[clamp(36px,5vw,56px)] leading-[1.05] font-semibold tracking-tight text-fg">No page here</h1>
            <p className="mt-4 text-[17px]/[1.7] text-muted">
              The address may be mistyped, or the page may have moved. Everything on the site is in these four
              sections, or on the{" "}
              <Link href="/" className="link">
                home page
              </Link>
              .
            </p>
            <ul className="mt-8 grid gap-3 sm:grid-cols-2">
              {NAV_ITEMS.map((item) => (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className="glass glass-sm flex min-h-15 flex-col justify-center gap-0.5 rounded-[20px] px-4 py-3 text-fg transition-colors duration-(--dur-hover) hover:bg-glass-strong"
                  >
                    <span className="text-title">{item.label}</span>
                    <span className="text-sm text-muted">{item.description}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </main>
      <SiteFooter />
    </>
  );
}
