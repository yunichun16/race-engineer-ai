import "server-only";
import Link from "next/link";
import { reportTitles } from "@/lib/report/load";
import { reportsByGroup } from "@/lib/report/manifest";
import { HAIRLINE, MICRO, cx } from "./styles";

export interface DocListProps {
  /** The level of each group's heading: 3 under a section's h2 (the default), 2 on a page of its own. */
  level?: 2 | 3;
  /** The slug of the page it sits on, marked as the current one. */
  current?: string;
  className?: string;
}

/**
 * Every published report, grouped as the manifest groups them: one glass card per group, each
 * report a row with its name and its file. A server component: it reads the reports' titles at
 * build time (so it can't go inside a client component).
 */
export async function DocList({ level = 3, current, className }: DocListProps) {
  const titles = await reportTitles();
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <div className={cx("grid gap-4 sm:grid-cols-2", className)}>
      {reportsByGroup().map(({ group, reports }) => (
        <section key={group} className="glass rounded-card px-5 pt-4 pb-2 sm:px-6">
          <Heading className={cx("text-muted", MICRO)}>{group}</Heading>
          <ul className="mt-2">
            {reports.map((r, k) => (
              <li key={r.slug} className={cx(k > 0 && "border-t", HAIRLINE)}>
                <Link
                  href={`/report/${r.slug}`}
                  aria-current={r.slug === current ? "page" : undefined}
                  className="group grid min-h-11 content-center gap-0.5 py-2.5"
                >
                  <span className="font-semibold text-fg group-hover:underline group-aria-[current=page]:underline">
                    {r.label ?? titles[r.slug]}
                  </span>
                  <span className="font-mono text-xs text-muted">{r.file}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
