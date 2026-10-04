import Link from "next/link";
import { Num } from "@/components/findings/Num";
import { Card } from "@/components/ui/Card";

/**
 * How to read a style comparison (plan 7.4), on a glass card before the explorer: why style means
 * the difference between teammates, what a row, a whisker and a faded row mean, how often chance
 * alone gives a clear metric, and that the map's plane has a meaning but its axes don't
 * (report/m4_style.md, "What the style map means"). Every number comes from a claim.
 */
export function HowToRead({ className }: { className?: string }) {
  return (
    <Card as="section" className={className}>
      <h2 className="micro">How to read it</h2>
      <p className="text-[15px]/[1.65] text-fg">
        The model&apos;s corner embedding mostly identifies the car: teammates&apos; centroids sit at a median cosine of{" "}
        <Num id="carCos" link />, drivers of different teams near zero. So style here means the difference between teammates.
      </p>
      <p className="text-[15px]/[1.65] text-fg">
        Each row is driver A minus driver B; the whisker is the <Num id="styleCiLevel" /> interval, and faded means not clearly
        different. With seven metrics, pairs with no real difference still show at least one clear metric{" "}
        <Num id="clearChance" link /> of the time, so read single cells with care.
      </p>
      <p className="text-[15px]/[1.65] text-fg">
        On the style map, read the plane as a whole, not its axes: neither axis means anything on its own.
      </p>
      <p className="text-[15px]">
        <Link href="/report/m4-style" className="link inline-flex min-h-11 items-center gap-1 font-medium">
          How the styles are measured
          <span aria-hidden="true">→</span>
        </Link>
      </p>
    </Card>
  );
}
