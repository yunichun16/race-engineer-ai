import Link from "next/link";
import type { ExplorerLink } from "@/lib/chat/links";

/** "Open in Mistakes →" / "Open in Styles →" beside a chart card's title: an ink link, built from
 *  the chart's payload (lib/chat/links.ts), never from the model's words. */
export function ExplorerAction({ link }: { link: ExplorerLink }) {
  return (
    <Link href={link.href} className="link inline-flex min-h-11 items-center gap-1 text-sm pointer-fine:min-h-9">
      {link.label}
      <span aria-hidden="true">→</span>
    </Link>
  );
}
