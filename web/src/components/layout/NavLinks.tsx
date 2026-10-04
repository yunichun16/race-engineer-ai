"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV_ITEMS, isCurrent } from "./nav";

/**
 * The header's section links, in ink. The current section is marked for screen readers and drawn
 * as a raised glass segment in bold (an outline in forced colours, where fills are dropped).
 */
export function NavLinks() {
  const pathname = usePathname();
  return (
    <ul className="flex items-center gap-0.5">
      {NAV_ITEMS.map((item) => (
        <li key={item.href}>
          <Link
            href={item.href}
            aria-current={isCurrent(pathname, item.href) ? "page" : undefined}
            className={
              "inline-flex min-h-11 items-center rounded-full px-3.5 text-sm font-medium text-fg transition-colors duration-(--dur-hover) hover:bg-hairline " +
              "aria-[current=page]:bg-glass-strong aria-[current=page]:font-semibold " +
              "aria-[current=page]:shadow-[inset_0_1px_0_var(--highlight),inset_0_0_0_1px_var(--edge)] " +
              "forced-colors:aria-[current=page]:outline-2 forced-colors:aria-[current=page]:outline-[Highlight]"
            }
          >
            {item.label}
          </Link>
        </li>
      ))}
    </ul>
  );
}

/** The phone header's shortcut to the chat, in lime (the header's one primary action); hidden on the chat itself. */
export function ChatPill() {
  const pathname = usePathname();
  if (isCurrent(pathname, "/chat")) return null;
  return (
    <Link
      href="/chat"
      className={
        "inline-flex min-h-11 items-center rounded-full bg-accent px-3.5 text-[15px] font-semibold text-accent-ink max-[360px]:px-3 " +
        "shadow-[inset_0_0_0_1px_var(--accent-edge)] transition-colors duration-(--dur-hover) hover:bg-(--accent-hover) " +
        "forced-colors:border forced-colors:border-[ButtonText]"
      }
    >
      Chat
    </Link>
  );
}
