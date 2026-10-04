import { Skeleton } from "@/components/ui/Skeleton";

/**
 * The explorer's place before its script runs (the page's Suspense fallback): blocks the size of
 * the examples, the pickers and the chart, so nothing moves when the explorer arrives.
 */
export function ExplorerSkeleton() {
  return (
    <div className="mt-10 grid grid-cols-[minmax(0,1fr)] gap-8 sm:mt-12">
      <Skeleton height={190} label="Loading the style explorer…" />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-8 lg:grid-cols-[20rem_minmax(0,1fr)] lg:items-start lg:gap-10">
        <Skeleton height={460} label="" />
        <Skeleton height={1120} label="" />
      </div>
    </div>
  );
}
