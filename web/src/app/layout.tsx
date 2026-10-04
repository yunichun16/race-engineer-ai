import type { Metadata, Viewport } from "next";
import "./globals.css";
// Every chart's scoped stylesheet, loaded once for the whole site (plan 4.4).
import "@/charts/styles/index.css";
import { BRAND } from "@/components/brand/brand";
import { SiteHeader } from "@/components/layout/SiteHeader";
import { SkipLink } from "@/components/layout/SkipLink";
import { SITE_URL } from "@/lib/site-url";
import { THEME_SCRIPT } from "@/lib/theme";

export const metadata: Metadata = {
  // The site's own origin (lib/site-url.ts), so the Open Graph and Twitter image tags that
  // opengraph-image.tsx and twitter-image.tsx add are absolute URLs, as the scrapers need.
  metadataBase: new URL(SITE_URL),
  title: { default: "Race Engineer AI", template: "%s · Race Engineer AI" },
  description:
    "Deep learning on F1 telemetry: the corners where drivers lost time, and how teammates drive differently. An unofficial fan project, so the findings can be wrong.",
  // Added to an iPhone's home screen, it opens full screen under a see-through status bar; the
  // header's top gap and the menu sheet clear it (safe-area insets, globals.css).
  appleWebApp: { capable: true, title: "Race Engineer", statusBarStyle: "black-translucent" },
};

// One browser-bar colour, the dark page, because Dark is the first-visit theme; the theme store
// re-syncs it to the light page for Light and System-light after hydration (lib/theme.ts).
// viewport-fit=cover lets env(safe-area-inset-*) report the status bar and the home indicator.
export const viewport: Viewport = {
  themeColor: BRAND.navy,
  colorScheme: "dark light",
  viewportFit: "cover",
};

// System fonts only (tokens.css), so the build never downloads a font.
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // The head script sets data-theme before React hydrates, hence suppressHydrationWarning.
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* The page's one inline script: the saved theme (Dark when nothing is saved), applied before the first paint. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="flex min-h-dvh flex-col antialiased">
        <SkipLink />
        {/* The pools of light and the grid behind every page (globals.css). */}
        <div className="ambient" aria-hidden="true" />
        <SiteHeader />
        {children}
      </body>
    </html>
  );
}
