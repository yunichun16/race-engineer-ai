import type { ReactNode } from "react";
import { Reveal } from "@/components/motion/Reveal";

export interface SectionHeadProps {
  /** The heading's id, for the section's aria-labelledby. */
  id: string;
  /** The micro-label that names the section ("How it's built"). */
  label: string;
  children: ReactNode;
}

/** A landing section's head: a micro-label, then the serif h2, fading up as it scrolls in. */
export function SectionHead({ id, label, children }: SectionHeadProps) {
  return (
    <Reveal className="mb-6 grid max-w-[760px] gap-2.5">
      <p className="micro">{label}</p>
      <h2 id={id} className="font-serif text-h2">
        {children}
      </h2>
    </Reveal>
  );
}

/** The landing's section box: the page container with the section rhythm. */
export const SECTION = "mx-auto max-w-[1120px] px-4 py-12 sm:px-6 lg:py-15";
