export interface SkeletonProps {
  /** Reserved height (a number is pixels), so the content arriving doesn't shift the page. */
  height: number | string;
  /** What is loading, read by screen readers: "Finding the flagged corners…". */
  label: string;
  className?: string;
}

/** A placeholder block with a slow sweep of light; it holds still when the reader prefers reduced motion. */
export function Skeleton({ height, label, className }: SkeletonProps) {
  return (
    <div className={["relative", className].filter(Boolean).join(" ")} style={{ height }}>
      <div aria-hidden="true" className="shimmer h-full rounded-xl bg-hairline" />
      <span className="sr-only">{label}</span>
    </div>
  );
}
