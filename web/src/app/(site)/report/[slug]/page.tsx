import type { Metadata } from "next";
import Link from "next/link";
import { Markdown, MarkdownInlines } from "@/components/report/Markdown";
import { ReportToc } from "@/components/report/ReportToc";
import { LINK, MICRO, cx } from "@/components/report/styles";
import { loadReport, reportTitles } from "@/lib/report/load";
import { REPORTS, neighbours } from "@/lib/report/manifest";
import { plainText } from "@/lib/report/markdown";
import type { PageDoc } from "@/lib/report/omit";
import { tocItems } from "@/lib/report/toc";

// Every report file the manifest publishes, prerendered at build from ../report; nothing else.
// A slug outside the manifest gets the 404 page in `next dev` and `next build`. Should one reach
// the page anyway (a wrong-case URL on a case-insensitive disk under `next start`), loadReport
// throws rather than calling notFound(), which would overwrite the good page (P0 spike 6).
export const dynamicParams = false;

export function generateStaticParams() {
  return REPORTS.map((r) => ({ slug: r.slug }));
}

/** The first paragraph, cut to about a search snippet's length, for the page description. */
function describe(doc: PageDoc): string | undefined {
  const first = doc.blocks.find((b) => b.type === "paragraph");
  if (first?.type !== "paragraph") return undefined;
  const text = plainText(first.children).replace(/\s+/g, " ").trim();
  if (text.length <= 160) return text;
  return `${text.slice(0, 157).replace(/\s+\S*$/, "")}…`;
}

export async function generateMetadata({ params }: PageProps<"/report/[slug]">): Promise<Metadata> {
  const { slug } = await params;
  const { entry, doc } = await loadReport(slug);
  return { title: doc.title?.text ?? entry.label ?? slug, description: describe(doc) };
}

export default async function ReportPage({ params }: PageProps<"/report/[slug]">) {
  const { slug } = await params;
  const { entry, doc, figures } = await loadReport(slug);
  const titles = await reportTitles();
  const toc = tocItems(doc.headings);
  const { previous, next } = neighbours(slug);

  return (
    <div className="mx-auto max-w-[1120px] px-4 pt-8 sm:px-6 sm:pt-12">
      {/* The header and the links at the foot set 17 px, like the article, so their 72ch is the
          article's width; every piece of text in them sets its own size. */}
      <header className="max-w-[72ch] text-[17px]">
        <p className={cx("text-muted", MICRO)}>
          <Link href="/report" className={LINK}>
            Report
          </Link>
          <span aria-hidden="true"> · </span>
          {entry.group}
        </p>
        <h1 className="mt-3 text-[clamp(36px,5vw,56px)] leading-[1.05] font-semibold tracking-tight text-balance text-fg">
          {doc.title ? <MarkdownInlines nodes={doc.titleInlines} /> : (entry.label ?? slug)}
        </h1>
        {doc.meta ? (
          <p className="mt-4 text-sm/[1.6] text-muted">
            <MarkdownInlines nodes={doc.meta} />
          </p>
        ) : null}
      </header>

      <div className={cx("mt-8 sm:mt-10", toc.length > 0 && "lg:grid lg:grid-cols-[minmax(0,1fr)_16rem] lg:gap-x-12")}>
        <ReportToc items={toc} variant="disclosure" className="mb-8" />
        <ReportToc items={toc} variant="sidebar" className="lg:col-start-2 lg:row-start-1" />
        <article className="max-w-[72ch] min-w-0 text-[17px]/[1.7] wrap-break-word text-fg lg:col-start-1 lg:row-start-1 [&>:first-child]:mt-0">
          <Markdown blocks={doc.blocks} figures={figures} />
        </article>
      </div>

      <nav aria-label="Reports" className="mt-14 grid max-w-[72ch] gap-4 border-t border-line pt-6 text-[17px] sm:grid-cols-2">
        {previous ? (
          <Link href={`/report/${previous.slug}`} className="group grid content-start gap-1">
            <span className={cx("text-muted", MICRO)}>
              <span aria-hidden="true">← </span>Previous
            </span>
            <span className="text-base font-medium text-fg group-hover:underline">{previous.label ?? titles[previous.slug]}</span>
          </Link>
        ) : (
          <span className="max-sm:hidden" />
        )}
        {next ? (
          <Link href={`/report/${next.slug}`} className="group grid content-start gap-1 sm:text-right">
            <span className={cx("text-muted", MICRO)}>
              Next<span aria-hidden="true"> →</span>
            </span>
            <span className="text-base font-medium text-fg group-hover:underline">{next.label ?? titles[next.slug]}</span>
          </Link>
        ) : null}
        <p className="sm:col-span-2">
          <Link href="/report" className={cx("inline-flex min-h-11 items-center text-base font-medium", LINK)}>
            <span aria-hidden="true">←&nbsp;</span>All reports
          </Link>
        </p>
      </nav>
    </div>
  );
}
