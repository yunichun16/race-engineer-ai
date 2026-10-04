import Link from "next/link";
import type { ReactNode } from "react";
import { EDGE, LINK, cx } from "./styles";

export interface FigureCardProps {
  /** Where the PNG is served: /report/figures/<file>. */
  src: string;
  /** What the figure shows, for readers who can't see it. */
  alt: string;
  /** The PNG's own size (lib/report/png.ts), so the page doesn't shift while it loads. */
  width: number;
  height: number;
  /** Under the card. When it repeats the alt text word for word, screen readers skip it. */
  caption?: ReactNode;
  /** A link after the caption to a page on the site, such as the report that discusses the figure. */
  source?: { href: string; label: string };
  /** On the `<figure>`. It has no margin of its own, so a grid or a report page sets the spacing. */
  className?: string;
}

/**
 * A report figure: a matplotlib PNG on a white paper card in both themes, never inverted (its
 * background is white), with the caption under the card in page colours and a link to the full
 * size image, which phones can zoom.
 */
export function FigureCard({ src, alt, width, height, caption, source, className }: FigureCardProps) {
  return (
    <figure className={className}>
      <div className={cx("rounded-2xl bg-(--paper) p-3", EDGE)}>
        {/* Image optimisation is off in M6 (plan decision 2): the PNG is served as it is, sized from its header. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt={alt}
          width={width}
          height={height}
          loading="lazy"
          decoding="async"
          className="block h-auto w-full"
        />
      </div>
      <figcaption className="mt-2.5 text-sm/[1.6] text-muted">
        {caption ? (
          <>
            <span aria-hidden={caption === alt ? true : undefined}>{caption}</span>
            <span aria-hidden="true"> · </span>
          </>
        ) : null}
        {source ? (
          <>
            Source:{" "}
            <Link href={source.href} className={LINK}>
              {source.label}
            </Link>
            <span aria-hidden="true"> · </span>
          </>
        ) : null}
        <a href={src} className={cx("whitespace-nowrap", LINK)}>
          Full size
        </a>
      </figcaption>
    </figure>
  );
}
