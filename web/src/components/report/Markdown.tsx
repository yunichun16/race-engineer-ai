import Link from "next/link";
import type { ReactNode } from "react";
import { InfoIcon } from "@/components/ui/icons";
import { REPO_URL } from "@/lib/env";
import { figureInfo } from "@/lib/report/figures";
import { EXTERNAL_REL, REPO_TITLE, resolveImage, resolveLink } from "@/lib/report/links";
import { plainText, type Align, type Block, type Inline } from "@/lib/report/markdown";
import { OMITTED_NOTE, type PageBlock } from "@/lib/report/omit";
import type { PngSize } from "@/lib/report/png";
import { FigureCard } from "./FigureCard";
import { CODE, EDGE, HAIRLINE, LINK, MICRO, cx } from "./styles";

/**
 * Renders a parsed report (lib/report/markdown.ts) as React elements. There is no HTML string
 * anywhere: every piece of report text is a React text node, so a `<` in a report is a character.
 * A server component; the whole page is prerendered at build.
 */
export function Markdown({
  blocks,
  figures,
}: {
  blocks: readonly PageBlock[];
  /** The size of each figure the report shows, by file name (lib/report/load.ts). */
  figures: Readonly<Record<string, PngSize>>;
}) {
  return <>{renderBlocks(blocks, { figures }, false)}</>;
}

/** Inline report text (the title, the generated line): code, emphasis and links. */
export function MarkdownInlines({ nodes }: { nodes: readonly Inline[] }) {
  return <>{renderInlines(nodes)}</>;
}

interface Ctx {
  figures: Readonly<Record<string, PngSize>>;
}

// ---- Blocks -------------------------------------------------------------------------------

function renderBlocks(blocks: readonly PageBlock[], ctx: Ctx, tight: boolean): ReactNode[] {
  return blocks.map((b, k) => {
    switch (b.type) {
      case "heading":
        return <HeadingBlock key={k} block={b} />;
      case "paragraph":
        // In a tight list item the text sits in the <li> itself, as GitHub draws it.
        return tight ? (
          <span key={k}>{renderInlines(b.children)}</span>
        ) : (
          <p key={k} className="my-5">
            {renderInlines(b.children)}
          </p>
        );
      case "list":
        return <ListBlock key={k} block={b} ctx={ctx} />;
      case "table":
        return <TableBlock key={k} block={b} />;
      case "code":
        return (
          <div
            key={k}
            role="region"
            aria-label={b.lang ? `Code (${b.lang})` : "Code"}
            tabIndex={0}
            className={cx("my-6 overflow-x-auto rounded-[14px] bg-raised", EDGE)}
          >
            <pre className="w-max min-w-full px-4 py-3.5 font-mono text-[13px] leading-[1.6]">
              <code>{b.text}</code>
            </pre>
          </div>
        );
      case "image":
        return <ImageBlock key={k} block={b} ctx={ctx} />;
      case "hr":
        return <hr key={k} className={cx("my-10 border-t", HAIRLINE)} />;
      case "omitted":
        return (
          <aside
            key={k}
            aria-label="Left out of this page"
            className="my-8 grid grid-cols-[20px_minmax(0,1fr)] gap-x-3 rounded-2xl border border-dashed border-line-strong px-4 py-3.5 text-[15px]/[1.6] text-muted"
          >
            <InfoIcon className="mt-0.5 size-5" />
            <p>{OMITTED_NOTE}</p>
          </aside>
        );
    }
  });
}

const HEADING_TEXT: Record<number, string> = {
  2: "mt-14 mb-4 font-serif text-[clamp(26px,3vw,36px)] leading-[1.15] font-normal tracking-[-0.01em]",
  3: "mt-10 mb-3 font-serif text-h3 font-normal",
};
const HEADING_SMALL = "mt-8 mb-2 text-[17px] leading-[1.3] font-semibold";

/**
 * A heading with its GitHub-style id and a "#" link to it. The link sits beside the heading, not
 * inside it, so the heading's accessible name stays its text; its own name says where it goes.
 */
function HeadingBlock({ block }: { block: Extract<Block, { type: "heading" }> }) {
  const level = Math.min(Math.max(block.level, 2), 6) as 2 | 3 | 4 | 5 | 6;
  const Tag = `h${level}` as const;
  return (
    <div className={cx("group text-balance text-fg first:mt-0", HEADING_TEXT[level] ?? HEADING_SMALL)}>
      <Tag id={block.id} className="inline">
        {renderInlines(block.children)}
      </Tag>{" "}
      <a
        href={`#${block.id}`}
        className="inline-block px-1 align-baseline font-sans text-[0.6em] font-normal text-muted no-underline hover:text-fg pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100 pointer-fine:focus-visible:opacity-100"
      >
        <span aria-hidden="true">#</span>
        <span className="sr-only">Link to the section “{block.text}”</span>
      </a>
    </div>
  );
}

function ListBlock({ block, ctx }: { block: Extract<Block, { type: "list" }>; ctx: Ctx }) {
  const items = block.items.map((item, k) => (
    <li key={k} className={cx("pl-1", block.tight ? null : "[&>p:first-child]:mt-0 [&>p:last-child]:mb-0")}>
      {renderBlocks(item, ctx, block.tight)}
    </li>
  ));
  const shape = cx("my-5 pl-6 marker:text-muted [li_&]:my-2", block.tight ? "space-y-1.5" : "space-y-3");
  return block.ordered ? (
    <ol start={block.start === 1 ? undefined : block.start} className={cx(shape, "list-decimal")}>
      {items}
    </ol>
  ) : (
    <ul className={cx(shape, "list-disc")}>{items}</ul>
  );
}

// A cell that is a number, an interval or a ratio, maybe with a unit: drawn in mono figures.
const NUMERIC = /^[^A-Za-z]*\d[^A-Za-z]*(?:\s?(?:s|ms|m|km\/h|x|MB|KB|kB|GB|Hz|min|h))?$/;

function TableBlock({ block }: { block: Extract<Block, { type: "table" }> }) {
  const numeric = block.head.map((_, c) => {
    const cells = block.rows.map((row) => plainText(row[c]).trim()).filter((t) => t !== "");
    return cells.length > 0 && cells.every((t) => NUMERIC.test(t));
  });
  const align = (c: number): Align => block.align[c] ?? (numeric[c] ? "right" : null);
  // A wide table scrolls inside its region rather than squeezing its words: a short cell keeps to
  // one line ("2025 (control)", a column name), and a long one keeps a readable width.
  const fit = (cell: Inline[]) => (plainText(cell).trim().length <= 24 ? "whitespace-nowrap" : "min-w-56");
  const alignClass = (a: Align) => (a === "right" ? "text-right" : a === "center" ? "text-center" : "text-left");
  const label = block.head.map((cell) => plainText(cell).trim()).filter(Boolean).join(", ");
  return (
    <div
      role="region"
      aria-label={label ? `Table: ${label}` : "Table"}
      tabIndex={0}
      className={cx("my-6 overflow-x-auto rounded-2xl", EDGE)}
    >
      <table className="w-full border-collapse text-[15px]/[1.5]">
        <thead>
          <tr>
            {block.head.map((cell, c) => (
              <th
                key={c}
                scope="col"
                className={cx("bg-raised px-3.5 py-2.5 align-bottom text-muted", MICRO, fit(cell), alignClass(align(c)))}
              >
                {renderInlines(cell)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.rows.map((row, r) => (
            <tr key={r}>
              {row.map((cell, c) => (
                <td
                  key={c}
                  className={cx(
                    "border-t px-3.5 py-2.5 align-top",
                    HAIRLINE,
                    alignClass(align(c)),
                    numeric[c] ? "font-mono text-[14px] whitespace-nowrap tabular-nums" : fit(cell),
                  )}
                >
                  {renderInlines(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ImageBlock({ block, ctx }: { block: Extract<Block, { type: "image" }>; ctx: Ctx }) {
  const target = resolveImage(block.src);
  const size = target.kind === "figure" ? ctx.figures[target.file] : undefined;
  if (target.kind !== "figure" || !size) {
    // The tests keep every published image a sized report figure; anything else shows its words.
    return <p className="my-5 text-muted">{block.alt}</p>;
  }
  // The site's description says what the figure shows; the report's alt text is its caption.
  const info = figureInfo(target.file);
  return (
    <FigureCard
      src={target.href}
      alt={info?.alt ?? block.alt}
      width={size.width}
      height={size.height}
      caption={block.alt}
      className="my-8"
    />
  );
}

// ---- Inlines ------------------------------------------------------------------------------

function renderInlines(nodes: readonly Inline[]): ReactNode[] {
  return nodes.map((n, k) => {
    switch (n.type) {
      case "text":
        return n.text;
      case "code":
        return (
          <code key={k} className={CODE}>
            {n.text}
          </code>
        );
      case "strong":
        return (
          <strong key={k} className="font-semibold">
            {renderInlines(n.children)}
          </strong>
        );
      case "em":
        return <em key={k}>{renderInlines(n.children)}</em>;
      case "link":
        return <LinkInline key={k} href={n.href} nodes={n.children} />;
    }
  });
}

/** A link as `lib/report/links.ts` resolves it: a page, a figure, an anchor, out, or a file in the repository. */
function LinkInline({ href, nodes }: { href: string; nodes: readonly Inline[] }) {
  const target = resolveLink(href, REPO_URL ?? undefined);
  const children = renderInlines(nodes);
  switch (target.kind) {
    case "report":
      return (
        <Link href={target.href} className={LINK}>
          {children}
        </Link>
      );
    case "figure":
    case "anchor":
      return (
        <a href={target.href} className={LINK}>
          {children}
        </a>
      );
    case "external":
      return (
        <a href={target.href} rel={EXTERNAL_REL} className={LINK}>
          {children}
        </a>
      );
    case "repo": {
      // Written as the file's own name ([m4_review_queue.csv](m4_review_queue.csv)), the text is
      // the reference; otherwise the text stays and the path follows it.
      const text = plainText([...nodes]).trim();
      const named = text === target.path || target.path.endsWith(`/${text}`);
      const code = (
        <code className={CODE} title={REPO_TITLE}>
          {named ? text : target.path}
        </code>
      );
      const reference = target.href ? (
        <a href={target.href} rel={EXTERNAL_REL} className={LINK}>
          {code}
        </a>
      ) : (
        <>
          {code}
          <span className="sr-only"> (in the repository)</span>
        </>
      );
      return named ? (
        reference
      ) : (
        <>
          {children} ({reference})
        </>
      );
    }
    case "invalid":
      return <>{children}</>;
  }
}
