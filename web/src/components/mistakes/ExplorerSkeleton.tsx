import { Skeleton } from "@/components/ui/Skeleton";

/**
 * The explorer's place in the prerendered page, until the browser renders it with the URL's
 * state (it reads `useSearchParams`, so it sits in a Suspense boundary): the picker card and the
 * list, at about their heights.
 */
export function ExplorerSkeleton() {
  return (
    <div className="mt-6 grid gap-6 sm:mt-8 lg:gap-8">
      <Skeleton height={168} label="Loading the mistake explorer…" />
      <div className="grid gap-6 lg:grid-cols-12 lg:gap-8">
        <Skeleton height={640} label="" className="lg:col-span-5" />
        <Skeleton height={640} label="" className="max-lg:hidden lg:col-span-7" />
      </div>
    </div>
  );
}
