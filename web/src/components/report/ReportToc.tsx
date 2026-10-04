import { Disclosure } from "@/components/ui/Disclosure";
import type { TocItem } from "@/lib/report/toc";
import { MICRO, cx } from "./styles";

export interface ReportTocProps {
  /** The headings to list, in order (lib/report/toc.ts makes them from a report). */
  items: readonly TocItem[];
  /**
   * "sidebar": a sticky glass card, shown from lg (put it in the right-hand column);
   * "disclosure": the "On this page" disclosure, shown below lg (put it above the article).
   */
  variant: "sidebar" | "disclosure";
  title?: string;
  className?: string;
}

/**
 * A page's contents: a sticky card beside the article on wide screens, a closed disclosure above
 * it on phones. Render both variants; each hides itself at the other's widths, so a screen reader
 * meets one list. The sticky card's text is ink, never muted (spec a: no muted text on sticky
 * surfaces).
 */
export function ReportToc({ items, variant, title = "On this page", className }: ReportTocProps) {
  if (items.length === 0) return null;
  const list = (
    <ol className="grid gap-0.5">
      {items.map((item) => (
        <li key={item.id}>
          <a
            href={`#${item.id}`}
            className={cx(
              "flex min-h-11 items-center rounded-[10px] px-2.5 py-1.5 text-fg hover:bg-hairline hover:underline pointer-fine:min-h-8",
              item.level === 3 ? "pl-6 text-[13px]/[1.4]" : "text-sm/[1.4]",
            )}
          >
            {item.text}
          </a>
        </li>
      ))}
    </ol>
  );
  if (variant === "disclosure") {
    return (
      <div className={cx("lg:hidden", className)}>
        <Disclosure summary={title}>
          <nav aria-label={title} className="-ml-2.5">
            {list}
          </nav>
        </Disclosure>
      </div>
    );
  }
  return (
    <div className={cx("hidden lg:block", className)}>
      <nav
        aria-label={title}
        className="glass sticky top-[calc(var(--header-h)+16px)] max-h-[calc(100dvh-var(--header-h)-32px)] overflow-y-auto rounded-card p-4"
      >
        <p className={cx("mb-2 px-2.5 text-fg", MICRO)}>{title}</p>
        {list}
      </nav>
    </div>
  );
}
