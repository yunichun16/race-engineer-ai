import Link from "next/link";
import { Mark } from "@/components/brand/Mark";
import { CodeIcon } from "@/components/ui/icons";
import { site } from "@/content/site";
import { ChatPill, NavLinks } from "./NavLinks";
import { MobileMenu } from "./MobileMenu";
import { ThemeToggle } from "./ThemeToggle";

/**
 * The floating glass pill on every page, sticky 12 px from the top (more in the installed iOS
 * app, where --header-top adds the status bar). From 640 px: the lockup, the four sections and
 * the theme toggle, plus a source-code link from 768 px once NEXT_PUBLIC_REPO_URL is set (M7;
 * the footer carries the same link at every width). Below 640 px: the lockup, a Chat shortcut
 * and the Menu button. From 640 to 767 px the wordmark hides (it stays for screen readers) so the
 * full navigation fits. Below 360 px (a 320 px phone) the lockup and the two buttons tighten
 * their spacing so the pill still fits; should a wider font still not fit, the wordmark ends in
 * an ellipsis rather than pushing the Menu button off the screen. All its text is ink: report
 * figures scroll under it.
 */
export function SiteHeader() {
  return (
    <header className="glass-nav sticky top-(--header-top) z-40 mx-auto mt-(--header-top) flex h-(--header-pill) w-[min(100%-24px,880px)] shrink-0 items-center gap-1 rounded-full p-1">
      <Link
        href="/"
        className="mr-auto inline-flex min-h-11 min-w-0 items-center gap-2 rounded-full pr-3 pl-2.5 text-fg max-[360px]:gap-2 max-[360px]:pr-2 sm:mr-1 sm:max-md:pr-1"
      >
        <Mark variant="dot" className="size-6 max-[360px]:size-5" />
        {/* The DOM text is "Race Engineer AI"; CSS uppercases it, so screen readers don't spell it out. */}
        <span className="min-w-0 truncate font-mono text-xs font-medium tracking-[0.14em] uppercase max-[379px]:text-[11px] max-[379px]:tracking-[0.1em] max-[360px]:tracking-[0.04em] sm:max-md:sr-only">
          {site.name}
        </span>
      </Link>
      <nav aria-label="Main" className="mr-auto hidden sm:block">
        <NavLinks />
      </nav>
      <ThemeToggle className="hidden sm:block" />
      {site.repoUrl ? (
        <a
          href={site.repoUrl}
          title="Source code"
          className="hidden size-11 items-center justify-center rounded-full text-fg hover:bg-hairline md:inline-flex"
        >
          <CodeIcon className="size-5" />
          <span className="sr-only">Source code</span>
        </a>
      ) : null}
      <div className="flex shrink-0 items-center gap-1 max-[360px]:gap-0.5 sm:hidden">
        <ChatPill />
        <MobileMenu />
      </div>
    </header>
  );
}
