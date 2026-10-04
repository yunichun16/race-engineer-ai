import "server-only";
import { FIGURES, figureInfo } from "@/lib/report/figures";
import { figureSize, reportTitles } from "@/lib/report/load";
import { reportBySlug } from "@/lib/report/manifest";
import { FigureCard } from "./FigureCard";

export interface ReportFigureProps {
  /** A file in report/figures, as `FIGURES` in lib/report/figures.ts lists it. */
  file: string;
  /** Replaces the figure's title from `FIGURES` under the card. */
  caption?: string;
  className?: string;
}

/**
 * One of the report's figures, by file name: its size from the PNG header, its alt text and title
 * from `FIGURES`, and a link to the report that discusses it. A server component (it reads the PNG
 * at build time). An unknown file fails the build.
 */
export async function ReportFigure({ file, caption, className }: ReportFigureProps) {
  const info = figureInfo(file);
  if (!info) throw new Error(`${file} is not in FIGURES (lib/report/figures.ts): ${FIGURES.map((f) => f.file).join(", ")}`);
  const { width, height } = await figureSize(file);
  const report = info.report ? reportBySlug(info.report) : undefined;
  const titles = report ? await reportTitles() : {};
  return (
    <FigureCard
      src={`/report/figures/${file}`}
      alt={info.alt}
      width={width}
      height={height}
      caption={caption ?? info.title}
      source={report ? { href: `/report/${report.slug}`, label: report.label ?? titles[report.slug] } : undefined}
      className={className}
    />
  );
}
