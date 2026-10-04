import Link from "next/link";
import { Mark } from "@/components/brand/Mark";
import { PRIVACY_NOTE } from "@/components/chat/privacy";
import { site } from "@/content/site";

// Links that stand on their own (not inside a sentence) get a 44 px tall target for thumbs.
const TARGET = "link inline-flex min-h-11 items-center";

// The human review's page (m4_review.md) isn't published: it lists the reviewer's per-finding
// verdicts, so the footer doesn't link it.
const FOOTER_LINKS = [
  { href: "/report", label: "Report" },
  { href: "/report/dataset-card", label: "Dataset card" },
];

/**
 * The footer of the content pages, on the bare page under a hairline (not glass): a small lockup
 * and the footer links, then what the project is (and isn't), that it can be wrong, the data's
 * terms, what the chat's limits keep about a visitor (M7) and the credit. Muted text, never faint.
 */
export function SiteFooter() {
  const { credit } = site;
  return (
    <footer className="mt-10 border-t border-hairline">
      <div className="mx-auto grid max-w-[1120px] gap-3 px-4 pt-6 pb-[max(40px,env(safe-area-inset-bottom))] text-sm/[1.6] text-muted sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
          <Link href="/" className="inline-flex min-h-11 items-center gap-2 text-fg">
            <Mark variant="dot" className="size-5" />
            <span className="font-mono text-[11px] font-medium tracking-[0.14em] whitespace-nowrap uppercase">{site.name}</span>
          </Link>
          <nav aria-label="Footer">
            <ul className="flex flex-wrap gap-x-5">
              {FOOTER_LINKS.map((link) => (
                <li key={link.href}>
                  <Link href={link.href} className={TARGET}>
                    {link.label}
                  </Link>
                </li>
              ))}
              {site.repoUrl ? (
                <li>
                  <a href={site.repoUrl} className={TARGET}>
                    Source code
                  </a>
                </li>
              ) : null}
            </ul>
          </nav>
        </div>
        <p className="max-w-[72ch] font-medium text-fg">{site.caveat}</p>
        <p className="max-w-[72ch]">
          {site.unofficial} F1 data from{" "}
          <a href={site.fastf1Url} className="link">
            FastF1
          </a>{" "}
          for non-commercial, educational use; not redistributed. Code {site.licence}-licensed.
        </p>
        <p className="max-w-[72ch]">{PRIVACY_NOTE}</p>
        {credit ? (
          <p className="flex flex-wrap items-center gap-x-2">
            <span>Built by {credit.name}</span>
            {credit.links.map((link) => (
              <span key={link.href} className="inline-flex items-center gap-x-2">
                <span aria-hidden="true">·</span>
                <a href={link.href} className={TARGET}>
                  {link.label}
                </a>
              </span>
            ))}
          </p>
        ) : null}
      </div>
    </footer>
  );
}
